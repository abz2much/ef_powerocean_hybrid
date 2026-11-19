"""Sensors whose value only the EcoFlow cloud has, for a PowerOcean Single Phase."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .catalogue import CATALOGUE_86, CatalogueEntry, CloudSource, cloud_value
from .cloud.coordinator import CloudCoordinator
from .const import CONF_CLOUD_SERIAL, DOMAIN
from .ids import system_id
from .models import InverterModel

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
