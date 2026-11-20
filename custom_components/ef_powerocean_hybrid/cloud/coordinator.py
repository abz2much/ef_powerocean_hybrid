"""Polls the EcoFlow cloud over HTTP for one system.

It never takes the entry down: it logs in inside its own updates, never in a
first refresh, so a rejected login only starts Home Assistant's sign-in flow
and a cloud outage only marks the cloud's values as failed. The HTTP part of
powerocean_single_phase's coordinator (v1.0.1), MIT, (c) 2026 niltrip, abz2much.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Protocol, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..catalogue import (
    CATALOGUE_86,
    HEARTBEAT,
    HEARTBEAT_TIME,
    STREAM,
    STREAM_TIME,
    CloudSource,
    cloud_value,
)
from ..const import (
    CONF_CLOUD_SERIAL,
    CONF_LIVE_POWER,
    CONF_MAX_SCHEDULE_POWER,
    DOMAIN,
)
from ..sources import Reading
from .api import (
    AuthenticationError,
    EcoflowApi,
    EcoflowApiError,
    EcoflowConnectionError,
)
from .flows import energy_flows
from .mqtt import (
    ENERGY_STREAM_POWER,
    ENERGY_STREAM_STRINGS,
    MODULE_METER,
    SCHEDULE_ADD,
    SCHEDULE_EDIT,
    SCHEDULE_REPEAT_DAILY,
    SCHEDULE_REPEAT_DAYS,
    ModuleVersion,
    MqttCallbacks,
    PowerOceanMqtt,
    Schedule,
    new_seq,
)
from .parse import parse
from .schedule_rules import MAX_SCHEDULES, refuse, validate_schedule
from .schedules import schedule_title, windows_overlap

_LOGGER = logging.getLogger(__name__)

# The only cloud model this integration reads: the PowerOcean Single Phase.
CLOUD_MODEL = "86"
CLOUD_SCAN_INTERVAL = timedelta(seconds=10)
# How often the system is asked for its full state again (module firmware).
MQTT_REFRESH_INTERVAL = timedelta(hours=6)
# How often the energy stream is asked for (option live power); the system
# sends it for ~25 s after each request.
ENERGY_STREAM_INTERVAL = timedelta(seconds=20)
# How long the stream's values win over the older HTTP ones (the cloud shows
# them later), as powerocean_single_phase.
ENERGY_STREAM_HOLD = timedelta(seconds=30)
# The joined values the energy stream feeds, by its datapoint: those whose
# cloud source is a value directly under "data". The state of charge is not.
STREAM_FEEDS: dict[str, str] = {
    entry.cloud.key: entry.key
    for entry in CATALOGUE_86
    if entry.kind == "joined"
    and entry.cloud is not None
    and entry.cloud.report is None
    and entry.cloud.key in ENERGY_STREAM_POWER.values()
}


# How long the system has to acknowledge a command, in seconds.
COMMAND_TIMEOUT = 10
# Sent together with the backup reserve: datapoint -> report it is read from.
# Not from the values directly under "data": those can be 0 while the report
# holds the setting.
SOC_LIMITS = {
    "sysBatChgUpLimit": "_EMS_CHANGE_REPORT",
    "sysBatDsgDownLimit": "_EMS_CHANGE_REPORT",
    "devSoc": "_EMS_PARAM_CHANGE_REPORT",
}
# Settings only known from the MQTT reports (EMS parameter change report).
MQTT_SETTINGS = ("batterySaveMode", "sysZone")
# How often the schedules are asked for (the system does not report changes,
# e.g. when it moves to the next schedule by itself).
SCHEDULE_POLL_INTERVAL = timedelta(seconds=60)
# Seconds between two readings while waiting for the list to show a change.
SCHEDULE_CONFIRM_DELAY = 0.5
# How long a setting the system confirmed over MQTT wins over the HTTP
# response: the cloud shows a change only 20-60 s later.
SETTING_HOLD = timedelta(minutes=2)


PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)
# The reports whose time shows how fresh the joined values are.
REPORT_TIMES = ((STREAM_TIME, STREAM), (HEARTBEAT_TIME, HEARTBEAT))


class CloudApi(Protocol):
    """What the coordinator needs from EcoflowApi."""

    sn: str
    variant: str
    token: str | None

    user_id: str | None

    async def async_authorize(self) -> None: ...

    async def async_mqtt_credentials(self) -> dict[str, Any]: ...

    async def fetch_raw(self) -> dict[str, Any]: ...


def create_api(hass: HomeAssistant, entry: ConfigEntry) -> CloudApi:
    """Return the cloud API for the entry's credentials."""
    return create_api_for(
        hass,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_CLOUD_SERIAL],
    )


def create_api_for(
    hass: HomeAssistant, email: str, password: str, serial: str
) -> CloudApi:
    """Return the cloud API for an account and the serial of its inverter."""
    return EcoflowApi(
        serialnumber=serial,
        username=email,
        password=password,
        variant=CLOUD_MODEL,
        session=async_get_clientsession(hass),
    )


async def async_check_cloud(api: CloudApi) -> str | None:
    """Log in and read the inverter; return the setup form's error key, if any."""
    try:
        await api.async_authorize()
        await api.fetch_raw()
    except AuthenticationError:
        return "invalid_auth"
    except EcoflowConnectionError:
        return "cloud_cannot_connect"
    except EcoflowApiError:
        # Logged in, but the account has no inverter with this serial.
        return "cloud_unknown_device"
    return None


class CloudCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """The parsed values of the cloud response, keyed by the parser's unique_id."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: CloudApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="EcoFlow cloud",
            update_interval=CLOUD_SCAN_INTERVAL,
        )
        self.api = api
        # MQTT live readings by catalogue key; filled once MQTT is added.
        self.live: dict[str, Reading] = {}
        # When Home Assistant last saw each report's time change, by time key.
        self.report_seen: dict[str, datetime] = {}
        self._report_times: dict[str, Any] = {}
        # When the last poll succeeded, on Home Assistant's clock.
        self.last_success_at: datetime | None = None
        # The last raw response, for the diagnostics and to tell a dual system.
        self.last_response: dict[str, Any] | None = None
        # The cloud's values the last energy stream updated, and when.
        self._stream_values: dict[str, float | int] = {}
        self._stream_time: datetime | None = None
        # Firmware by device serial, shown on their device pages: the packs'
        # from HTTP, the smart meter's from MQTT.
        self.firmware: dict[str, str] = {}
        self._http_firmware: dict[str, str] = {}
        self._mqtt_firmware: dict[str, str] = {}
        # The optional MQTT connection (option "mqtt").
        self.mqtt: PowerOceanMqtt | None = None
        self.modules: dict[int, ModuleVersion] = {}
        self._mqtt_refresh_unsub: Callable[[], None] | None = None
        self._energy_stream_unsub: Callable[[], None] | None = None
        # Charge limit, backup reserve and devSoc (see SOC_LIMITS).
        self.soc_limits: dict[str, int] = {}
        # Battery save mode and sysZone (see MQTT_SETTINGS).
        self.mqtt_settings: dict[str, int] = {}
        # Settings confirmed over MQTT the HTTP response may not show yet:
        # name -> (value, time of the confirmation).
        self._recent_settings: dict[str, tuple[int, datetime]] = {}
        # Commands waiting for their acknowledgement, by seq.
        self._pending_acks: dict[int, asyncio.Future[None]] = {}
        # Scheduled tasks (option MQTT), by id; none known until the first list.
        self.schedules: dict[int, Schedule] = {}
        self._schedules_known = False
        # Highest id of a schedule we added: the list may not show it yet.
        self._last_added_id = 0
        self._schedule_poll_unsub: Callable[[], None] | None = None
        # Waiting for the list after a command.
        self._schedule_waiters: list[asyncio.Future[None]] = []
        # One schedule command at a time: the next id is the highest plus 1.
        self._schedule_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict[str, Any]:
        if self.api.token is None:
            await self._async_authorize()
        response = await self._async_fetch()
        self.last_response = response
        try:
            result = parse(response, self.api.sn, self.api.variant)
            values = result.values()
        except PARSE_ERRORS as err:
            msg = f"Unexpected cloud response: {err}"
            raise UpdateFailed(msg) from err
        self.soc_limits = soc_limits_of(response)
        self._update_firmware(result.firmware)
        self._note_report_times(values)
        self._hold_recent_settings(values)
        self._hold_energy_stream(values)
        self.last_success_at = dt_util.utcnow()
        return values

    def _hold_recent_settings(self, values: dict[str, Any]) -> None:
        """Keep settings confirmed over MQTT the HTTP response does not show yet.

        Otherwise a slider jumps back after a change, and the next command for
        the SOC_LIMITS would send the old values along again.
        """
        now = dt_util.utcnow()
        for name, (value, since) in list(self._recent_settings.items()):
            keys = self.setting_keys(name, values)
            if name in SOC_LIMITS:
                reported = self.soc_limits.get(name)
            else:
                reported = values.get(keys[0]) if keys else None
            if reported == value or now - since > SETTING_HOLD:
                del self._recent_settings[name]
                continue
            if name in SOC_LIMITS:
                self.soc_limits[name] = value
            for key in keys:
                values[key] = value

    def _update_firmware(self, firmware: dict[str, str]) -> None:
        """Show a changed firmware version on the device page.

        As the cloud integration does; devices not registered yet get the
        version from their entities.
        """
        self._http_firmware = firmware
        self.firmware = {**firmware, **self._mqtt_firmware}
        registry = dr.async_get(self.hass)
        entry = cast(ConfigEntry, self.config_entry)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            for domain, serial in device.identifiers:
                version = self.firmware.get(serial) if domain == DOMAIN else None
                if version and device.sw_version != version:
                    registry.async_update_device(device.id, sw_version=version)

    # Optional MQTT connection, as powerocean_single_phase runs it.

    async def async_start_mqtt(self) -> None:
        """Connect over MQTT; failures only log, and MQTT stays off until a reload."""
        if not self.api.user_id:
            _LOGGER.warning("MQTT: no user ID from the login, not connecting")
            return
        try:
            credentials = await self.api.async_mqtt_credentials()
            callbacks = MqttCallbacks(
                on_modules=self._from_mqtt_thread(self._apply_modules),
                on_settings=self._from_mqtt_thread(self._apply_settings),
                on_ack=self._from_mqtt_thread(self._apply_ack),
                on_energy_stream=self._from_mqtt_thread(self._apply_energy_stream),
                on_schedules=self._from_mqtt_thread(self._apply_schedules),
                on_schedule=self._from_mqtt_thread(self._apply_schedule),
                on_connected=self._connected_from_mqtt_thread,
            )
            client = PowerOceanMqtt(
                credentials, self.api.sn, self.api.user_id, callbacks
            )
            starting = self.hass.async_add_executor_job(client.start)
            try:
                await asyncio.shield(starting)
            except asyncio.CancelledError:
                # Unloaded while connecting: the unload's stop has run before
                # the client was kept, so stop it here once its start is done.
                starting.add_done_callback(
                    lambda _: self.hass.async_add_executor_job(client.stop)
                )
                raise
        except (EcoflowApiError, OSError, ValueError, KeyError) as err:
            _LOGGER.warning("MQTT not started: %s", err)
            return
        self.mqtt = client
        self._mqtt_refresh_unsub = async_track_time_interval(
            self.hass, self._async_request_full_state, MQTT_REFRESH_INTERVAL
        )
        if cast(ConfigEntry, self.config_entry).options.get(CONF_LIVE_POWER, False):
            self._energy_stream_unsub = async_track_time_interval(
                self.hass, self._async_request_energy_stream, ENERGY_STREAM_INTERVAL
            )
        self._schedule_poll_unsub = async_track_time_interval(
            self.hass, self._async_request_schedules, SCHEDULE_POLL_INTERVAL
        )
        # Mostly not connected yet: then the connection asks once it is up.
        await self._async_request_on_connect()
        # The settings become available
        self.async_update_listeners()

    async def async_stop_mqtt(self) -> None:
        """Close the MQTT connection, if any."""
        if self._mqtt_refresh_unsub:
            self._mqtt_refresh_unsub()
            self._mqtt_refresh_unsub = None
        if self._energy_stream_unsub:
            self._energy_stream_unsub()
            self._energy_stream_unsub = None
        if self._schedule_poll_unsub:
            self._schedule_poll_unsub()
            self._schedule_poll_unsub = None
        self.schedules = {}
        self._schedules_known = False
        self._last_added_id = 0
        if self.mqtt:
            client, self.mqtt = self.mqtt, None
            await self.hass.async_add_executor_job(client.stop)

    def _connected_from_mqtt_thread(self) -> None:
        """Hand the connection being up over from paho's thread."""
        self.hass.loop.call_soon_threadsafe(self._mqtt_connected)

    @callback
    def _mqtt_connected(self) -> None:
        """Ask for what a request sent before the connection was up missed."""
        if self.mqtt:
            cast(ConfigEntry, self.config_entry).async_create_background_task(
                self.hass, self._async_request_on_connect(), f"{DOMAIN} MQTT connected"
            )

    async def _async_request_on_connect(self) -> None:
        """Ask for the schedules, and the energy stream with live power."""
        now = dt_util.utcnow()
        if cast(ConfigEntry, self.config_entry).options.get(CONF_LIVE_POWER, False):
            await self._async_request_energy_stream(now)
        await self._async_request_schedules(now)

    def _mqtt_up(self) -> PowerOceanMqtt | None:
        """Return the connection if a request can go out on it now.

        While paho reconnects, a request would be lost; the disconnect is
        logged once, and the connection asks again once it is back.
        """
        return self.mqtt if self.mqtt is not None and self.mqtt.connected else None

    async def _async_request_full_state(self, _now: datetime) -> None:
        """Ask the system for its full state again (module firmware)."""
        if client := self._mqtt_up():
            await self.hass.async_add_executor_job(client.request_full_state)

    async def _async_request_energy_stream(self, _now: datetime) -> None:
        """Ask the system for the energy stream again (option live power)."""
        if client := self._mqtt_up():
            await self.hass.async_add_executor_job(client.request_energy_stream)

    @callback
    def _apply_energy_stream(self, values: dict[str, float | int]) -> None:
        """Hand the power values of an energy stream report to the joined values.

        With Home Assistant's receipt time: they count for 30 s, as long as the
        cloud integration holds them over the older HTTP values.
        """
        # Not while the connection is being closed; not for a dual system,
        # whose stream is not mapped yet (as powerocean_single_phase).
        if not self.mqtt or not self.data or self._is_dual():
            return
        now = dt_util.utcnow()
        for datapoint, key in STREAM_FEEDS.items():
            if datapoint in values:
                self.live[key] = Reading(float(values[datapoint]), now)
        # The cloud's own values (solar, PV strings, flows), as the cloud
        # integration shows them.
        self._stream_values = self._stream_datapoints(values)
        self._stream_time = now
        self.data = {**self.data, **self._stream_values}
        self.async_update_listeners()

    def _is_dual(self) -> bool:
        """Return True for a dual installation: its stream is not mapped yet."""
        response = self.last_response or {}
        data = response.get("data")
        return isinstance(data, dict) and bool(data.get("parallel"))

    def _stream_datapoints(
        self, values: dict[str, float | int]
    ) -> dict[str, float | int]:
        """Return the existing datapoints the energy stream updates, by unique_id.

        The values directly under "data" (sysLoadPwr, ...), the PV strings of
        the energy stream report and the flows calculated from the heartbeat,
        now calculated from the stream. Never adds a datapoint. As
        powerocean_single_phase (v1.0.1).
        """
        data = self.data or {}
        prefix = f"{self.api.sn}_"
        solar = float(values["mpptPwr"])
        by_suffix: dict[str, float | int] = {
            f"_mpptHeartBeat_{key}": value
            for key, value in energy_flows(solar, values["sysGridPwr"], values["bpPwr"])
        }
        by_suffix["_mpptHeartBeat_mpptPv_pwrTotal"] = solar
        for name in ENERGY_STREAM_STRINGS.values():
            if name in values:
                by_suffix[f"_ENERGY_STREAM_REPORT_{name}"] = values[name]

        found: dict[str, float | int] = {}
        for name, value in values.items():
            if f"{prefix}{name}" in data:
                found[f"{prefix}{name}"] = value
        for unique_id in data:
            if not unique_id.startswith(prefix):
                continue
            for suffix, value in by_suffix.items():
                if unique_id.endswith(suffix):
                    found[unique_id] = value
        return found

    def _hold_energy_stream(self, values: dict[str, Any]) -> None:
        """Keep the values of a recent energy stream over the older HTTP ones."""
        if self._stream_time is None:
            return
        if dt_util.utcnow() - self._stream_time > ENERGY_STREAM_HOLD:
            self._stream_values = {}
            self._stream_time = None
            return
        for unique_id, value in self._stream_values.items():
            if unique_id in values:
                values[unique_id] = value

    def _from_mqtt_thread[T](self, apply: Callable[[T], None]) -> Callable[[T], None]:
        """Return a callback that hands a value over from paho's thread."""

        def hand_over(value: T) -> None:
            self.hass.loop.call_soon_threadsafe(apply, value)

        return hand_over

    @property
    def _meter_device(self) -> str:
        """The smart meter's device, as the cloud integration names it."""
        return f"{self.api.sn}_meter{MODULE_METER}"

    @callback
    def _apply_modules(self, modules: dict[int, ModuleVersion]) -> None:
        """Keep the module firmware; the smart meter's goes on its device."""
        if not self.mqtt:
            # Arrived while the connection was being closed
            return
        self.modules = modules
        meter = modules.get(MODULE_METER)
        self._mqtt_firmware = {self._meter_device: meter.firmware} if meter else {}
        self._update_firmware(self._http_firmware)

    @callback
    def clear_mqtt_firmware(self) -> None:
        """Remove the firmware a former MQTT connection showed (option off)."""
        if self._meter_device in self._http_firmware:
            return
        registry = dr.async_get(self.hass)
        entry = cast(ConfigEntry, self.config_entry)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (DOMAIN, self._meter_device) in device.identifiers and device.sw_version:
                registry.async_update_device(device.id, sw_version=None)

    # Settings: LED brightness, backup reserve, solar surplus, battery save mode

    def setting_keys(
        self, name: str, values: dict[str, Any] | None = None
    ) -> list[str]:
        """Return the datapoints of the inverter with a setting (emsCtrlLedBright)."""
        prefix = f"{self.api.sn}_"
        return [
            unique_id
            for unique_id in (self.data if values is None else values) or {}
            if unique_id.startswith(prefix) and unique_id.endswith(f"_{name}")
        ]

    def setting(self, name: str) -> int | None:
        """Return the current value of a setting of the inverter."""
        if name in SOC_LIMITS:
            return self.soc_limits.get(name)
        if name in MQTT_SETTINGS:
            return self.mqtt_settings.get(name)
        keys = self.setting_keys(name)
        value = self.data.get(keys[0]) if keys else None
        return value if isinstance(value, int) else None

    async def async_set_led_brightness(self, percent: int) -> None:
        """Set the LED brightness over MQTT and wait for the acknowledgement.

        Raises:
            HomeAssistantError: Without MQTT connection, or if the system does
                not acknowledge the command in time.
        """
        mqtt = self._connected_mqtt()
        await self._async_command(mqtt.set_led_brightness, percent)
        # Acknowledged: show the value before the system reports it
        self._apply_settings({"emsCtrlLedBright": percent})

    async def async_set_backup_reserve(self, percent: int) -> None:
        """Set the backup reserve (sysBatDsgDownLimit), see _async_set_soc_limits."""
        await self._async_set_soc_limits({"sysBatDsgDownLimit": percent})

    async def async_set_solar_surplus(self, percent: int) -> None:
        """Set the surplus solar energy delivery (devSoc), see _async_set_soc_limits."""
        await self._async_set_soc_limits({"devSoc": percent})

    async def _async_set_soc_limits(self, changes: dict[str, int]) -> None:
        """Change one of the SOC_LIMITS over MQTT and wait for the acknowledgement.

        One command carries charge limit, backup reserve and devSoc; the others
        are sent unchanged, so all must be known.

        Raises:
            HomeAssistantError: Without MQTT connection, without the current
                values, or if the system does not acknowledge the command in
                time.
        """
        mqtt = self._connected_mqtt()
        limits = {**self.soc_limits, **changes}
        if any(name not in limits for name in SOC_LIMITS):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="soc_limits_unknown"
            )
        await self._async_command(
            mqtt.set_soc_limits,
            limits["sysBatChgUpLimit"],
            limits["sysBatDsgDownLimit"],
            limits["devSoc"],
        )
        self._apply_settings(changes)

    async def async_set_battery_save_mode(self, *, enabled: bool) -> None:
        """Switch the battery save mode over MQTT and wait for the acknowledgement.

        The command carries sysZone too; it is sent unchanged, so it must be
        known.

        Raises:
            HomeAssistantError: Without MQTT connection, without sysZone, or if
                the system does not acknowledge the command in time.
        """
        mqtt = self._connected_mqtt()
        zone = self.mqtt_settings.get("sysZone")
        if zone is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="sys_zone_unknown"
            )
        await self._async_command(mqtt.set_battery_save_mode, int(enabled), zone)
        self._apply_settings({"batterySaveMode": int(enabled)})

    def _connected_mqtt(self) -> PowerOceanMqtt:
        if not self.mqtt:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="mqtt_not_connected"
            )
        return self.mqtt

    async def _async_command(self, send: Callable[..., None], *values: Any) -> None:
        """Send a command (blocking, with the seq last) and wait for the ack."""
        seq = new_seq()
        acknowledged = self.hass.loop.create_future()
        self._pending_acks[seq] = acknowledged
        try:
            await self.hass.async_add_executor_job(partial(send, *values, seq))
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await acknowledged
        except TimeoutError as err:
            mqtt = self.mqtt
            _LOGGER.warning(
                "MQTT command %s not acknowledged within %s s (MQTT connected: %s)",
                seq,
                COMMAND_TIMEOUT,
                mqtt.connected if mqtt is not None else False,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_acknowledged"
            ) from err
        finally:
            self._pending_acks.pop(seq, None)

    @callback
    def _apply_ack(self, seq: int) -> None:
        """Complete the command the system acknowledged."""
        acknowledged = self._pending_acks.get(seq)
        if acknowledged and not acknowledged.done():
            acknowledged.set_result(None)

    @callback
    def _apply_settings(self, values: dict[str, int]) -> None:
        """Show changed settings (sensors and numbers) without polling."""
        if not self.mqtt:
            # Arrived while the connection was being closed
            return
        now = dt_util.utcnow()
        for name, value in values.items():
            self._recent_settings[name] = (value, now)
        soc_limits = {**self.soc_limits}
        mqtt_settings = {**self.mqtt_settings}
        data = {**self.data}
        for name, value in values.items():
            if name in SOC_LIMITS:
                soc_limits[name] = value
            if name in MQTT_SETTINGS:
                mqtt_settings[name] = value
            for key in self.setting_keys(name):
                data[key] = value
        if (soc_limits, mqtt_settings, data) == (
            self.soc_limits,
            self.mqtt_settings,
            self.data,
        ):
            return
        self.soc_limits = soc_limits
        self.mqtt_settings = mqtt_settings
        self.data = data
        self.async_update_listeners()

    # Scheduled tasks (option MQTT)

    @property
    def max_schedule_power(self) -> int | None:
        """Return the option "Max schedule power (W)", None if it is not set."""
        value = cast(ConfigEntry, self.config_entry).options.get(
            CONF_MAX_SCHEDULE_POWER
        )
        return value if type(value) is int and value >= 1 else None

    @property
    def schedules_known(self) -> bool:
        """Return True once the system has sent its list of schedules."""
        return self._schedules_known

    @property
    def active_schedule(self) -> Schedule | None:
        """Return the schedule the system is using now, None without one."""
        return next((s for s in self.schedules.values() if s.in_use), None)

    async def _async_request_schedules(self, _now: datetime) -> None:
        """Ask the system for its schedules again (it reports no changes)."""
        if client := self._mqtt_up():
            await self.hass.async_add_executor_job(client.request_schedules)

    @callback
    def _apply_schedules(self, schedules: list[Schedule]) -> None:
        """Take over the list of schedules, ours or the app's."""
        if not self.mqtt:
            # Arrived while the connection was being closed
            return
        new = {schedule.schedule_id: schedule for schedule in schedules}
        changed = new != self.schedules or not self._schedules_known
        self.schedules = new
        self._schedules_known = True
        for waiter in self._schedule_waiters:
            if not waiter.done():
                waiter.set_result(None)
        if changed:
            self.async_update_listeners()

    @callback
    def _apply_schedule(self, schedule: Schedule) -> None:
        """Take over one schedule (the answer to a single read)."""
        if not self.mqtt or self.schedules.get(schedule.schedule_id) == schedule:
            return
        self.schedules = {**self.schedules, schedule.schedule_id: schedule}
        self.async_update_listeners()

    def _check_overlap(self, schedule: Schedule) -> None:
        """Refuse a time that overlaps another schedule, whatever the days."""
        for other in self.schedules.values():
            if other.schedule_id != schedule.schedule_id and windows_overlap(
                schedule.start, schedule.end, other.start, other.end
            ):
                refuse("schedule_overlap", other=schedule_title(other))

    def _known_schedule(self, schedule_id: int) -> Schedule:
        if schedule_id not in self.schedules:
            refuse("schedule_not_found", schedule_id=schedule_id)
        return self.schedules[schedule_id]

    async def _async_read_schedules(self, mqtt: PowerOceanMqtt) -> None:
        """Ask for the list and wait for the next one."""
        waiter = self.hass.loop.create_future()
        self._schedule_waiters.append(waiter)
        try:
            await self.hass.async_add_executor_job(mqtt.request_schedules)
            await waiter
        finally:
            self._schedule_waiters.remove(waiter)

    async def _async_confirm_schedules(
        self, mqtt: PowerOceanMqtt, shown: Callable[[dict[int, Schedule]], bool]
    ) -> None:
        """Read the list until it shows what a command did.

        The system acknowledges a command before its list shows the change.
        Going on with the old list would reuse an id or miss an overlap.

        Raises:
            HomeAssistantError: If the list does not show it within
                COMMAND_TIMEOUT. The command was acknowledged, so it may still
                take effect.
        """
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                while True:
                    await self._async_read_schedules(mqtt)
                    if shown(self.schedules):
                        return
                    await asyncio.sleep(SCHEDULE_CONFIRM_DELAY)
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="schedule_not_confirmed"
            ) from err

    async def async_set_schedule_enabled(
        self, schedule_id: int, *, enabled: bool
    ) -> None:
        """Switch a schedule on or off, wait for the acknowledgement, read again.

        Raises:
            HomeAssistantError: Without MQTT connection, for an unknown
                schedule, or if the system does not acknowledge the command.
        """
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            self._known_schedule(schedule_id)
            await self._async_command(
                mqtt.set_schedule_enabled, schedule_id, int(enabled)
            )
            await self._async_confirm_schedules(
                mqtt,
                lambda shown: (
                    schedule_id in shown and shown[schedule_id].enabled is enabled
                ),
            )

    async def async_delete_schedule(self, schedule_id: int) -> None:
        """Delete a schedule, wait for the acknowledgement, read again."""
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            self._known_schedule(schedule_id)
            await self._async_command(mqtt.delete_schedule, schedule_id)
            await self._async_confirm_schedules(
                mqtt, lambda shown: schedule_id not in shown
            )

    async def async_add_schedule(
        self,
        *,
        schedule_type: int,
        power: int,
        start: int,
        end: int,
        days: int = 0,
        enabled: bool = True,
    ) -> int:
        """Add a schedule with the next free id and return the id.

        Start and end are minutes since midnight. `days` is 0 for daily, or a
        bit mask (Monday 1 to Sunday 64).

        Raises:
            HomeAssistantError: Without MQTT connection or list of schedules,
                for invalid values (see validate_schedule), or if the system
                does not acknowledge the command.
        """
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            if not self._schedules_known:
                refuse("schedules_unknown")
            if len(self.schedules) >= MAX_SCHEDULES:
                refuse("schedule_limit_reached", max=MAX_SCHEDULES)
            schedule = Schedule(
                schedule_id=max(max(self.schedules, default=0), self._last_added_id)
                + 1,
                schedule_type=schedule_type,
                enabled=enabled,
                power=power,
                start=start,
                end=end,
                repeat=SCHEDULE_REPEAT_DAILY if days == 0 else SCHEDULE_REPEAT_DAYS,
                days=days,
            )
            validate_schedule(schedule, self.max_schedule_power)
            self._check_overlap(schedule)
            await self._async_command(mqtt.save_schedule, SCHEDULE_ADD, schedule)
            self._last_added_id = schedule.schedule_id
            await self._async_confirm_schedules(
                mqtt,
                lambda shown: _same_schedule(shown.get(schedule.schedule_id), schedule),
            )
            return schedule.schedule_id

    async def async_update_schedule(
        self,
        schedule_id: int,
        *,
        schedule_type: int | None = None,
        power: int | None = None,
        start: int | None = None,
        end: int | None = None,
        days: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Change a schedule: send the whole schedule, what is not given is kept.

        The power is only checked against the option when it changes, the days
        only when they change (older schedules show repeat 65, kept as it is)
        and the time for overlaps only when it changes.

        Raises:
            HomeAssistantError: Without MQTT connection, for an unknown
                schedule, invalid values (see validate_schedule), or if the
                system does not acknowledge the command.
        """
        async with self._schedule_lock:
            mqtt = self._connected_mqtt()
            current = self._known_schedule(schedule_id)
            changes: dict[str, Any] = {
                name: value
                for name, value in (
                    ("schedule_type", schedule_type),
                    ("power", power),
                    ("start", start),
                    ("end", end),
                    ("enabled", enabled),
                )
                if value is not None
            }
            if days is not None:
                changes["days"] = days
                changes["repeat"] = (
                    SCHEDULE_REPEAT_DAILY if days == 0 else SCHEDULE_REPEAT_DAYS
                )
            schedule = replace(current, **changes)
            validate_schedule(
                schedule,
                self.max_schedule_power,
                check_power=power is not None,
                check_days=days is not None,
            )
            if start is not None or end is not None:
                self._check_overlap(schedule)
            await self._async_command(mqtt.save_schedule, SCHEDULE_EDIT, schedule)
            await self._async_confirm_schedules(
                mqtt,
                lambda shown: _same_schedule(shown.get(schedule_id), schedule),
            )

    def _note_report_times(self, values: dict[str, Any]) -> None:
        """Record when each report's time changed, on Home Assistant's clock.

        A time seen for the first time proves nothing: after a restart a dead
        cloud still returns its last report.
        """
        for time_key, report in REPORT_TIMES:
            current = cloud_value(values, self.api.sn, CloudSource(time_key, report))
            if current is None:
                # Missing from this response: not a change when it comes back.
                continue
            if (
                time_key in self._report_times
                and current != self._report_times[time_key]
            ):
                self.report_seen[time_key] = dt_util.utcnow()
            self._report_times[time_key] = current

    async def _async_fetch(self) -> dict[str, Any]:
        """Fetch the response, logging in again once if it was rejected."""
        try:
            return await self.api.fetch_raw()
        except EcoflowConnectionError as err:
            msg = f"EcoFlow cloud not reachable: {err}"
            raise UpdateFailed(msg) from err
        except EcoflowApiError as err:
            # Typically an expired token
            _LOGGER.debug("Cloud request rejected (%s), logging in again", err)
        await self._async_authorize()
        try:
            return await self.api.fetch_raw()
        except EcoflowApiError as err:
            msg = f"EcoFlow cloud request failed: {err}"
            raise UpdateFailed(msg) from err

    async def _async_authorize(self) -> None:
        try:
            await self.api.async_authorize()
        except AuthenticationError as err:
            # Outside a first refresh this starts the sign-in flow and leaves
            # the entry, and its Modbus side, running.
            msg = "EcoFlow cloud rejected the login"
            raise ConfigEntryAuthFailed(msg) from err
        except EcoflowApiError as err:
            msg = f"EcoFlow cloud login failed: {err}"
            raise UpdateFailed(msg) from err


def _same_schedule(shown: Schedule | None, sent: Schedule) -> bool:
    """Return True if the list shows a schedule as it was sent."""
    # In use and the unknown field 2 are the system's, not part of what is sent
    return shown is not None and replace(
        shown, in_use=False, unknown_2=None
    ) == replace(sent, in_use=False, unknown_2=None)


def soc_limits_of(response: dict[str, Any]) -> dict[str, int]:
    """Return charge limit, backup reserve and devSoc from the reports."""
    data = response.get("data")
    quota = data.get("quota") if isinstance(data, dict) else None
    if not isinstance(quota, dict):
        return {}
    found: dict[str, int] = {}
    for report, values in quota.items():
        if not isinstance(values, dict):
            continue
        for name, suffix in SOC_LIMITS.items():
            value = values.get(name)
            if report.endswith(suffix) and type(value) is int:
                found[name] = value
    return found
