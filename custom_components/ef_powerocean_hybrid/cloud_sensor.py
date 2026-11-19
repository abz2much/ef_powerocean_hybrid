"""Sensors whose value only the EcoFlow cloud has, for a PowerOcean Single Phase."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, cast

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .catalogue import CATALOGUE_86, CatalogueEntry, CloudSource, cloud_value
from .cloud.coordinator import CloudCoordinator
from .const import CONF_CLOUD_SERIAL, DOMAIN
from .ids import system_id
from .models import InverterModel

_LOGGER = logging.getLogger(__name__)

# Faster than any PowerOcean Single Phase can charge or discharge its battery,
# so a rise beyond it is a bad reading, not energy.
COUNTER_MAX_POWER_W = 10_000
COUNTER_RESOLUTION_KWH = 0.01

_PACK_SOC = re.compile(r"^(?P<serial>.+?)_(?:[A-Z0-9]+_)?BP_STA_REPORT_bpSoc$")


def pack_serials(values: dict[str, Any], inverter_serial: str) -> list[str]:
    """Return the serials of the battery packs in the cloud values."""
    serials = {
        match["serial"]
        for unique_id in values
        if (match := _PACK_SOC.match(unique_id)) and match["serial"] != inverter_serial
    }
    return sorted(serials)


class CloudSensor(CoordinatorEntity[CloudCoordinator], SensorEntity):
    """One value of the cloud response, on the inverter or a battery pack."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CloudCoordinator,
        entry: ConfigEntry,
        definition: CatalogueEntry,
        pack: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._definition = definition
        # Every cloud entry of the catalogue has a cloud source.
        self._source = cast(CloudSource, definition.cloud)
        self._system = system_id(entry)
        # The serial the cloud keys this value by.
        self._serial = pack or entry.data[CONF_CLOUD_SERIAL]
        self._pack = pack
        description = definition.description
        self._attr_unique_id = f"{pack or self._system}_{definition.key}"
        self._attr_translation_key = definition.key
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_entity_category = description.entity_category
        self._attr_entity_registry_enabled_default = definition.enabled

    @property
    def device_info(self) -> DeviceInfo:
        if self._pack is None:
            # The cloud is read for one model only.
            return DeviceInfo(
                identifiers={(DOMAIN, self._system)},
                name="EcoFlow PowerOcean",
                manufacturer="EcoFlow",
                model=InverterModel.POWEROCEAN_SINGLE_PHASE.traits.display_name,
            )
        return DeviceInfo(
            identifiers={(DOMAIN, self._pack)},
            name=f"Battery {self._pack}",
            manufacturer="EcoFlow",
            model="PowerOcean Battery",
            serial_number=self._pack,
        )

    def _value(self) -> Any:
        # Only asked while the coordinator's last update succeeded, so with data.
        value = cloud_value(self.coordinator.data, self._serial, self._source)
        description = self._definition.description
        expects_number = (
            description.unit or description.device_class or description.state_class
        )
        if isinstance(value, str) and expects_number:
            # Text such as "FALSE" where a number or a time belongs.
            return None
        return value

    @property
    def available(self) -> bool:
        return super().available and self._value() is not None

    @property
    def native_value(self) -> Any:
        # Only asked while available, so the value is there.
        value = self._value()
        if self.device_class is SensorDeviceClass.TIMESTAMP:
            return datetime.fromtimestamp(float(value), UTC)
        if (
            isinstance(value, float)
            and value.is_integer()
            and self._definition.description.unit in (None, "Wh")
        ):
            return int(value)
        return value


def cloud_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: CloudCoordinator,
    add_entities: Callable[[Iterable[Entity]], None],
) -> Callable[[], None]:
    """Add the cloud's sensors, and those of packs that appear later.

    Returns the function that stops watching for new packs.
    """
    inverter = [e for e in CATALOGUE_86 if e.kind == "cloud" and not e.per_pack]
    per_pack = [e for e in CATALOGUE_86 if e.kind == "cloud" and e.per_pack]
    added: set[str] = set()

    @callback
    def add_packs() -> None:
        packs = [
            serial
            for serial in pack_serials(
                coordinator.data or {}, entry.data[CONF_CLOUD_SERIAL]
            )
            if serial not in added
        ]
        if not packs:
            return
        added.update(packs)
        add_entities(
            CloudSensor(coordinator, entry, definition, pack)
            for pack in packs
            for definition in per_pack
        )
        _link_packs(hass, entry, packs)

    add_entities(CloudSensor(coordinator, entry, definition) for definition in inverter)
    add_packs()
    return coordinator.async_add_listener(add_packs)


@callback
def _link_packs(hass: HomeAssistant, entry: ConfigEntry, packs: list[str]) -> None:
    """Put the packs' devices under the inverter's.

    via_device in the device info is deprecated since Home Assistant 2026.10, so
    the devices are made first and then linked with async_update_device.
    """
    registry = dr.async_get(hass)
    inverter = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, system_id(entry))}
    )
    for pack in packs:
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, pack)},
            name=f"Battery {pack}",
            manufacturer="EcoFlow",
            model="PowerOcean Battery",
            serial_number=pack,
        )
        if device.via_device_id != inverter.id:
            registry.async_update_device(device.id, via_device_id=inverter.id)


class CloudCounter(CoordinatorEntity[CloudCoordinator], RestoreSensor):
    """A battery energy total counted from the cloud, on an entry whose fixed
    counter source is the cloud.

    It keeps its last value while the cloud is offline or leaves the value out,
    and after a restart. A reading of 0, or lower than the last one, counts as
    missing, so the Energy dashboard never sees a gap or a drop.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: CloudCoordinator,
        entry: ConfigEntry,
        definition: CatalogueEntry,
    ) -> None:
        super().__init__(coordinator)
        self._source = cast(CloudSource, definition.cloud)
        self._serial = entry.data[CONF_CLOUD_SERIAL]
        self._value: float | None = None
        # When the shown value was taken; a rise is judged against it.
        self._accepted_at: datetime | None = None
        self._spike_logged = False
        system = system_id(entry)
        self._attr_unique_id = f"{system}_{definition.key}_cloud"
        self._attr_translation_key = f"{definition.key}_cloud"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, system)})

    @property
    def available(self) -> bool:
        # The last value stands while the cloud is away.
        return True

    @property
    def native_value(self) -> float | None:
        return self._value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._value = cast(float | None, last.native_value)
            if (state := await self.async_get_last_state()) is not None:
                self._accepted_at = state.last_updated
        self._take()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._take()
        super()._handle_coordinator_update()

    def _take(self) -> None:
        if not self.coordinator.last_update_success:
            return
        value = cloud_value(self.coordinator.data or {}, self._serial, self._source)
        if not isinstance(value, int | float) or value <= 0:
            return
        now = dt_util.utcnow()
        if self._value is not None and self._accepted_at is not None:
            # Never down: on a total_increasing sensor any drop reads as a
            # meter reset, and the Energy dashboard would count the whole
            # lifetime again. Never faster than the battery can charge either:
            # a spike, once booked, would hold the counter there for good.
            hours = max((now - self._accepted_at).total_seconds(), 0) / 3600
            believable = COUNTER_MAX_POWER_W * hours / 1000 + COUNTER_RESOLUTION_KWH
            if not 0 <= value - self._value <= believable:
                if value > self._value and not self._spike_logged:
                    _LOGGER.warning(
                        "Ignoring an implausible rise of %s from %s to %s kWh",
                        self.entity_id,
                        self._value,
                        value,
                    )
                    self._spike_logged = True
                return
        self._value = float(value)
        self._accepted_at = now
        self._spike_logged = False


class HeldCounter(RestoreSensor):
    """A Modbus energy total shown at its last value while Modbus is away.

    Made only when the entry runs on the cloud because Modbus did not answer at
    start. It keeps the unique ID of the Modbus sensor, which takes over again,
    from Modbus's own saved totals, once the entry reloads with Modbus.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _unrecorded_attributes = frozenset({"source"})

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        system = system_id(entry)
        self._attr_unique_id = f"{system}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, system)})
        self._attr_extra_state_attributes = {"source": "modbus (held)"}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value
