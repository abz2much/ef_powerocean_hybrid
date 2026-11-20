"""The LED brightness, a setting Modbus and the cloud both have, as one entity.

While Modbus is online it is the Modbus integration's number: read from and
written to register 40541. Otherwise it is the cloud integration's: read from
the cloud and written over MQTT. It keeps the unique ID of the Modbus number, so
its entity ID, name, labels and area never change when the cloud is added.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN, WRITABLE_NUMBERS_MAP
from .ids import system_id
from .sources import modbus_reading

if TYPE_CHECKING:
    from .cloud.coordinator import CloudCoordinator
    from .coordinator import EcoflowCoordinator

LED_NUMBER: Final = next(
    number
    for number in WRITABLE_NUMBERS_MAP
    if number.key == "device_led_brightness_control"
)
# The cloud's datapoint of the same setting
LED_DATAPOINT: Final = "emsCtrlLedBright"
# Checked this often even when neither source sends an update, so the entity
# hands over to the cloud on time when Modbus goes quiet.
RECHECK: Final = timedelta(seconds=10)

type Side = Literal["modbus", "cloud"]


class JoinedLedNumber(NumberEntity):
    """The LED brightness of the inverter in %."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    # Changes with every switch of source; not worth a row in the recorder.
    _unrecorded_attributes = frozenset({"source"})

    def __init__(
        self,
        entry: ConfigEntry,
        modbus: EcoflowCoordinator | None,
        cloud: CloudCoordinator,
    ) -> None:
        self._modbus = modbus
        self._cloud = cloud
        system = system_id(entry)
        self._attr_unique_id = f"{system}_{LED_NUMBER.key}"
        self._attr_translation_key = LED_NUMBER.key
        self._attr_native_min_value = LED_NUMBER.min_value
        self._attr_native_max_value = LED_NUMBER.max_value
        self._attr_native_step = LED_NUMBER.step
        self._attr_native_unit_of_measurement = LED_NUMBER.unit
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, system)})
        # What the entity last showed: available, value, source.
        self._shown: tuple[bool, float | None, Side | None] | None = None

    def _pick(self) -> tuple[float, Side] | None:
        """Return the value and its side: Modbus while online, else the cloud."""
        if self._modbus is not None and (
            reading := modbus_reading(
                self._modbus, LED_NUMBER.read_key, dt_util.utcnow()
            )
        ):
            return reading.value, "modbus"
        cloud = self._cloud
        # As the cloud integration's number: only with an MQTT connection.
        if (
            cloud.last_update_success
            and cloud.mqtt is not None
            and (value := cloud.setting(LED_DATAPOINT)) is not None
        ):
            return value, "cloud"
        return None

    def update_from_sources(self) -> bool:
        """Pick the value once; return whether what the entity shows changed."""
        picked = self._pick()
        shown: tuple[bool, float | None, Side | None] = (
            (True, picked[0], picked[1]) if picked else (False, None, None)
        )
        if shown == self._shown:
            return False
        self._shown = shown
        self._attr_available, value, source = shown
        self._attr_native_value = (
            int(value) if value is not None and float(value).is_integer() else value
        )
        self._attr_extra_state_attributes = {"source": source}
        return True

    async def async_added_to_hass(self) -> None:
        self.update_from_sources()
        if self._modbus is not None:
            # The context makes the Modbus coordinator read this register.
            self.async_on_remove(
                self._modbus.async_add_listener(
                    self._updated, frozenset({LED_NUMBER.read_key})
                )
            )
        self.async_on_remove(self._cloud.async_add_listener(self._updated))
        self.async_on_remove(
            async_track_time_interval(self.hass, self._rechecked, RECHECK)
        )

    @callback
    def _updated(self) -> None:
        if self.update_from_sources():
            self.async_write_ha_state()

    @callback
    def _rechecked(self, _now: datetime) -> None:
        self._updated()

    async def async_set_native_value(self, value: float) -> None:
        """Write over the side the value comes from; raises if not confirmed."""
        picked = self._pick()
        if self._modbus is not None and picked is not None and picked[1] == "modbus":
            await self._modbus.async_write_modbus_register(
                entity_def=LED_NUMBER, value=round(value)
            )
        else:
            await self._cloud.async_set_led_brightness(int(value))
        self._updated()
