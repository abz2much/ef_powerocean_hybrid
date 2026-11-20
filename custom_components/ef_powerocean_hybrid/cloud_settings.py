"""Settings sent to the system over the EcoFlow app's MQTT.

Only with the option MQTT: the surplus solar energy delivery and the battery
save mode, set as the EcoFlow app does. The LED brightness and the backup
reserve, which Modbus shows too, are in joined_number.py. The number and switch
entities of powerocean_single_phase (v1.0.1), MIT, (c) 2026 niltrip, abz2much.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud.coordinator import SOC_LIMITS, CloudCoordinator
from .const import CONF_MQTT, DOMAIN
from .ids import system_id


def cloud_settings[S: CloudSetting](
    hass: HomeAssistant, entry: ConfigEntry, settings: list[S]
) -> list[S]:
    """Return the settings if the MQTT option is on.

    Switched off: remove them instead of leaving them unavailable.
    """
    if entry.options.get(CONF_MQTT, False):
        return settings
    registry = er.async_get(hass)
    for setting in settings:
        if entity_id := registry.async_get_entity_id(
            setting.platform_domain, DOMAIN, str(setting.unique_id)
        ):
            registry.async_remove(entity_id)
    return []


def setting_numbers(
    hass: HomeAssistant, entry: ConfigEntry, cloud: CloudCoordinator
) -> list[CloudPercentSetting]:
    """Return the numbers, if the MQTT option is on."""
    # The backup reserve is a joined number (joined_number.py).
    numbers: list[CloudPercentSetting] = [CloudSolarSurplus(cloud, entry)]
    return cloud_settings(hass, entry, numbers)


def setting_switches(
    hass: HomeAssistant, entry: ConfigEntry, cloud: CloudCoordinator
) -> list[CloudBatterySaveMode]:
    """Return the switches, if the MQTT option is on."""
    return cloud_settings(hass, entry, [CloudBatterySaveMode(cloud, entry)])


class CloudSetting(CoordinatorEntity[CloudCoordinator]):
    """A setting of the inverter, written over MQTT."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    platform_domain: str
    _attr_translation_key: str

    def __init__(self, coordinator: CloudCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        system = system_id(entry)
        self._attr_unique_id = f"{system}_{self._attr_translation_key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, system)})


class CloudPercentSetting(CloudSetting, NumberEntity):
    """A setting of the inverter in %, as in the EcoFlow app."""

    platform_domain = "number"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_unit_of_measurement = PERCENTAGE
    # Datapoint that holds the current value
    _datapoint: str

    @property
    def available(self) -> bool:
        """Return False without MQTT connection or without a current value."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.native_value is not None
        )

    @property
    def native_value(self) -> int | None:
        return self.coordinator.setting(self._datapoint)


class CloudSocLimit(CloudPercentSetting):
    """A setting sent in one command with the other SOC_LIMITS, in steps of 1 %."""

    _attr_native_step = 1

    @property
    def available(self) -> bool:
        """Return False also without the other values, which are sent along."""
        return super().available and all(
            name in self.coordinator.soc_limits for name in SOC_LIMITS
        )


class CloudSolarSurplus(CloudSocLimit):
    """Surplus solar energy delivery of the app in % (devSoc)."""

    _attr_translation_key = "solar_surplus"
    _datapoint = "devSoc"

    async def async_set_native_value(self, value: float) -> None:
        """Send the new value; raises if the system does not confirm it."""
        await self.coordinator.async_set_solar_surplus(int(value))


class CloudBatterySaveMode(CloudSetting, SwitchEntity):
    """Battery save mode of the app, only reported over MQTT."""

    platform_domain = "switch"
    _attr_translation_key = "battery_save_mode"

    @property
    def available(self) -> bool:
        """Return False without MQTT connection, state or sysZone (sent along)."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.is_on is not None
            and self.coordinator.setting("sysZone") is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the state the system reported last."""
        value = self.coordinator.setting("batterySaveMode")
        return None if value is None else bool(value)

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Switch on; raises if the system does not confirm it."""
        await self.coordinator.async_set_battery_save_mode(enabled=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Switch off; raises if the system does not confirm it."""
        await self.coordinator.async_set_battery_save_mode(enabled=False)
