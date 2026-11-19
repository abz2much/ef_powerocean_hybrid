"""Polls the EcoFlow cloud over HTTP for one system.

It never takes the entry down: it logs in inside its own updates, never in a
first refresh, so a rejected login only starts Home Assistant's sign-in flow
and a cloud outage only marks the cloud's values as failed. The HTTP part of
powerocean_single_phase's coordinator (v1.0.1), MIT, (c) 2026 niltrip, abz2much.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..catalogue import (
    HEARTBEAT,
    HEARTBEAT_TIME,
    STREAM,
    STREAM_TIME,
    CloudSource,
    cloud_value,
)
from ..const import CONF_CLOUD_SERIAL
from ..sources import Reading
from .api import (
    AuthenticationError,
    EcoflowApi,
    EcoflowApiError,
    EcoflowConnectionError,
)
from .parse import parse

_LOGGER = logging.getLogger(__name__)

# The only cloud model this integration reads: the PowerOcean Single Phase.
CLOUD_MODEL = "86"
CLOUD_SCAN_INTERVAL = timedelta(seconds=10)
PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)
# The reports whose time shows how fresh the joined values are.
REPORT_TIMES = ((STREAM_TIME, STREAM), (HEARTBEAT_TIME, HEARTBEAT))


class CloudApi(Protocol):
    """What the coordinator needs from EcoflowApi."""

    sn: str
    variant: str
    token: str | None

    async def async_authorize(self) -> None: ...

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

    async def _async_update_data(self) -> dict[str, Any]:
        if self.api.token is None:
            await self._async_authorize()
        response = await self._async_fetch()
        try:
            values = parse(response, self.api.sn, self.api.variant).values()
        except PARSE_ERRORS as err:
            msg = f"Unexpected cloud response: {err}"
            raise UpdateFailed(msg) from err
        self._note_report_times(values)
        return values

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
