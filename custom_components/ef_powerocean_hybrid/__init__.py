"""EF PowerOcean Hybrid: local Modbus TCP integration for EcoFlow PowerOcean."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .cloud import coordinator as cloud_module
from .cloud.coordinator import CloudCoordinator
from .const import (
    CONF_CLOUD_SERIAL,
    CONF_COUNTER_SOURCE,
    CONF_HOST,
    CONF_MQTT,
    CONF_PORT,
    DOMAIN,
)
from .control import ControlInputs
from .coordinator import (
    EcoflowConfigEntry,
    EcoflowCoordinator,
    held_totals_issue_id,
    identity_mismatch_issue_id,
    state_store,
)
from .frontend_card import async_register_card
from .modbus import async_prepare
from .reading_checks import KEYS as READING_CHECK_KEYS
from .runtime import HybridData
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

# How often an entry running on the cloud checks whether Modbus answers again.
MODBUS_RETRY_INTERVAL: Final = timedelta(seconds=30)

CONFIG_SCHEMA: Final = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]


def _modbus_disabled_issue_id(entry: ConfigEntry) -> str:
    """Return the stable id of the repairs issue raised while Modbus is disabled."""
    return f"modbus_disabled_{entry.entry_id}"


async def async_setup(
    hass: HomeAssistant,
    config: ConfigType,  # noqa: ARG001 - required by the integration API
) -> bool:
    """Register the actions, which serve every configured inverter, and the card."""
    async_setup_services(hass)
    await async_register_card(hass)
    return True


@callback
def _async_update_modbus_disabled_issue(
    hass: HomeAssistant, entry: EcoflowConfigEntry, coordinator: EcoflowCoordinator
) -> None:
    """Raise a repairs issue while telemetry appears disabled, and clear it after."""
    issue_id = _modbus_disabled_issue_id(entry)
    if not coordinator.is_modbus_disabled:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="modbus_disabled",
        translation_placeholders={"host": coordinator.host},
        learn_more_url="https://github.com/abz2much/ef_powerocean_hybrid#prerequisites",
    )


@callback
def _async_claim_serial_number(
    hass: HomeAssistant, entry: EcoflowConfigEntry, coordinator: EcoflowCoordinator
) -> None:
    """Make the serial number the entry's unique id once the device has reported it.

    Entries made before the serial was read are keyed by host and port, which
    changes with the network while the inverter does not. Until then their
    entities and device are keyed by the entry id; they move to the serial here,
    keeping their entity IDs, names, labels and areas.
    """
    serial = coordinator.identity.serial_number
    if not serial or serial == "unknown" or entry.unique_id == serial:
        return
    if any(
        other.unique_id == serial and other.entry_id != entry.entry_id
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        _LOGGER.warning(
            "Serial number %s is already the unique id of another entry; "
            "keeping %s for this one",
            serial,
            entry.unique_id,
        )
        return
    _LOGGER.info(
        "Unique id of %s changes from %s to %s", entry.title, entry.unique_id, serial
    )
    _async_move_to_serial(hass, entry, serial)
    hass.config_entries.async_update_entry(entry, unique_id=serial)


@callback
def _async_move_to_serial(
    hass: HomeAssistant, entry: EcoflowConfigEntry, serial: str
) -> None:
    """Re-key the entities and the device made under the entry id by the serial."""
    old = f"{entry.entry_id}_"
    entities = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(entities, entry.entry_id):
        if registered.unique_id.startswith(old):
            entities.async_update_entity(
                registered.entity_id,
                new_unique_id=f"{serial}_{registered.unique_id[len(old) :]}",
            )
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if (DOMAIN, entry.entry_id) in device.identifiers:
            devices.async_update_device(device.id, new_identifiers={(DOMAIN, serial)})


@callback
def _async_make_device_physical(hass: HomeAssistant, entry: EcoflowConfigEntry) -> None:
    """Drop the service entry type the device was once registered with.

    The device info no longer sets one, which leaves what the registry has.
    """
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.entry_type is dr.DeviceEntryType.SERVICE:
            registry.async_update_device(device.id, entry_type=None)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Copy the settings of an older entry from its data to its options.

    Copied, not moved: an earlier release still finds them in the data, and as
    the version only goes up by a minor one, it still loads the entry.
    """
    if entry.version == 1 and entry.minor_version < 2:
        connection = {CONF_HOST, CONF_PORT}
        hass.config_entries.async_update_entry(
            entry,
            options={
                **{k: v for k, v in entry.data.items() if k not in connection},
                **entry.options,
            },
            minor_version=2,
        )
        _LOGGER.debug("Copied the settings of %s to its options", entry.title)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EcoflowConfigEntry) -> bool:
    """Set up EF PowerOcean Hybrid from a config entry."""

    if CONF_COUNTER_SOURCE not in entry.data:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_COUNTER_SOURCE: "modbus" if CONF_HOST in entry.data else "cloud",
            },
        )
    coordinator = None
    # An entry made with only a cloud connection has no inverter address.
    if CONF_HOST in entry.data:
        await async_prepare(hass)
        modbus = EcoflowCoordinator(hass, config_entry=entry)
        try:
            await _async_start_modbus(hass, entry, modbus)
        except ConfigEntryNotReady:
            if CONF_CLOUD_SERIAL not in entry.data:
                raise
            # The entry stays loaded, so Home Assistant does not shut it down.
            await modbus.async_shutdown()
            # The cloud carries the system until the inverter answers again.
            _LOGGER.warning(
                "The inverter does not answer over Modbus; running on the EcoFlow "
                "cloud until it does"
            )
            _async_watch_for_modbus(hass, entry)
        else:
            coordinator = modbus

    cloud = None
    if CONF_CLOUD_SERIAL in entry.data:
        cloud = CloudCoordinator(hass, entry, cloud_module.create_api(hass, entry))
        # Not a first refresh: a cloud that is down or rejects the login must
        # not stop the entry, or its Modbus side, from loading.
        await cloud.async_refresh()
    entry.runtime_data = HybridData(modbus=coordinator, cloud=cloud)
    _async_make_device_physical(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if cloud is not None:
        # Optional, as in powerocean_single_phase: a failed start does not stop
        # the setup; MQTT then stays off until a reload.
        if entry.options.get(CONF_MQTT, False):
            entry.async_on_unload(cloud.async_stop_mqtt)
            if cloud.last_update_success:
                await cloud.async_start_mqtt()
            else:
                _async_start_mqtt_with_cloud(entry, cloud)
        else:
            # Switched off: no outdated firmware on the device pages
            cloud.clear_mqtt_firmware()

    if coordinator is not None:
        # The control loop reads its inputs, and what its reading checks compare,
        # whatever entities are enabled. With it and the entities all asking, the
        # poll reads only what they ask for from here on.
        entry.async_on_unload(
            coordinator.async_require(ControlInputs.keys() | READING_CHECK_KEYS)
        )
        coordinator.async_poll_on_demand()

    # Reload integration when config entry data changes
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


@callback
def _async_start_mqtt_with_cloud(
    entry: EcoflowConfigEntry, cloud: CloudCoordinator
) -> None:
    """Start MQTT once, with the cloud's first answer.

    powerocean_single_phase retries its whole setup until the cloud answers
    (__init__.py:88) and starts MQTT then (:111). This entry loads without the
    cloud, so its MQTT waits for that answer instead. As there, a start that then
    fails is not retried: MQTT needs a reload.
    """
    unsubscribe: list[Callable[[], None]] = []

    @callback
    def _cloud_updated() -> None:
        # Called with the first successful poll: a coordinator does not call its
        # listeners while its polls keep failing.
        unsubscribe.pop()()
        entry.async_create_background_task(
            cloud.hass, cloud.async_start_mqtt(), f"{DOMAIN} MQTT start"
        )

    # Unloading shuts the cloud coordinator down, so no update arrives later.
    unsubscribe.append(cloud.async_add_listener(_cloud_updated))


async def _async_start_modbus(
    hass: HomeAssistant, entry: EcoflowConfigEntry, coordinator: EcoflowCoordinator
) -> None:
    """Connect to the inverter over Modbus through *coordinator*."""
    # Connects and reads the device, through the coordinator's _async_setup. If
    # it fails, Home Assistant retries the setup and shuts the coordinator down.
    await coordinator.async_config_entry_first_refresh()
    # Before the update listener, which would reload the entry over it.
    _async_claim_serial_number(hass, entry, coordinator)

    _async_update_modbus_disabled_issue(hass, entry, coordinator)
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _async_update_modbus_disabled_issue(hass, entry, coordinator)
        )
    )


@callback
def _async_watch_for_modbus(hass: HomeAssistant, entry: EcoflowConfigEntry) -> None:
    """Reload the entry once the inverter answers over Modbus again."""

    async def async_probe(_now: datetime) -> None:
        # Imported here: the config flow imports this package.
        from .config_flow import (
            DEVICE_SERIAL_NUMBER,
            async_read_device_settings,
        )

        settings = await async_read_device_settings(
            hass, entry.data[CONF_HOST], entry.data[CONF_PORT]
        )
        # Only this inverter: a connection alone, or another device at the
        # address, would fail the setup again and reload every 30 seconds.
        if settings and settings.get(DEVICE_SERIAL_NUMBER) == entry.unique_id:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        async_track_time_interval(hass, async_probe, MODBUS_RETRY_INTERVAL)
    )


async def _async_reload_entry(hass: HomeAssistant, entry: EcoflowConfigEntry) -> None:
    """Reload the integration when the config entry is updated."""
    _LOGGER.debug("Config entry updated — reloading EF PowerOcean Hybrid")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EcoflowConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    ir.async_delete_issue(hass, DOMAIN, _modbus_disabled_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, identity_mismatch_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, held_totals_issue_id(entry))
    # The coordinator's async_shutdown, registered with the entry, hands control
    # back and closes the connection after this returns.
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the state saved for the entry, which nothing else would read again."""
    await state_store(hass, entry.entry_id).async_remove()
