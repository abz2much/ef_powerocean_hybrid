"""A value Modbus and the cloud both measure, shown as one entity.

It takes Modbus while Modbus has a trusted, current reading, then the cloud's
fresh MQTT value, then its fresh HTTP value; otherwise it is unavailable. It
keeps the unique ID of the Modbus sensor it replaces, so its entity ID, name,
labels and area never change when the cloud is added or removed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .catalogue import HEARTBEAT_TIME, STREAM_TIME, CatalogueEntry, CloudSource
from .const import CONF_CLOUD_SERIAL, DOMAIN
from .display import shown_number
from .ids import system_id
from .models import InverterModel
from .sources import Source, http_reading, modbus_reading, mqtt_reading, pick

if TYPE_CHECKING:
    from .cloud.coordinator import CloudCoordinator
    from .coordinator import EcoflowCoordinator

MQTT_LIMIT: Final = timedelta(seconds=30)
# How long a cloud report counts as fresh after Home Assistant saw it change.
HTTP_LIMITS: Final = {
    STREAM_TIME: timedelta(seconds=60),
    HEARTBEAT_TIME: timedelta(seconds=120),
}
# Checked this often even when neither source sends an update, so a value
# whose source has gone quiet becomes unavailable on time.
RECHECK: Final = timedelta(seconds=10)


class JoinedSensor(SensorEntity):
    """One joined value of the catalogue."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Changes with every switch of source; not worth a row in the recorder.
    _unrecorded_attributes = frozenset({"source"})

    def __init__(
        self,
        entry: ConfigEntry,
        definition: CatalogueEntry,
        modbus: EcoflowCoordinator | None,
        cloud: CloudCoordinator | None,
        precision: int | None = None,
    ) -> None:
        self._key = definition.key
        # Every joined entry of the catalogue has a cloud source.
        self._source = cast(CloudSource, definition.cloud)
        self._modbus = modbus
        self._cloud = cloud
        self._cloud_serial = entry.data[CONF_CLOUD_SERIAL]
        description = definition.description
        self._attr_unique_id = f"{system_id(entry)}_{definition.key}"
        self._attr_translation_key = definition.key
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_entity_category = description.entity_category
        self._attr_suggested_display_precision = precision
        # What the entity last showed: available, value, source.
        self._shown: tuple[bool, float | None, Source | None] | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, system_id(entry))},
            name="EcoFlow PowerOcean",
            manufacturer="EcoFlow",
            model=InverterModel.POWEROCEAN_SINGLE_PHASE.traits.display_name,
        )

    def update_from_sources(self) -> bool:
        """Pick the value once; return whether what the entity shows changed."""
        now = dt_util.utcnow()
        cloud = self._cloud
        picked = pick(
            modbus_reading(self._modbus, self._key, now) if self._modbus else None,
            mqtt_reading(cloud, self._key) if cloud else None,
            http_reading(cloud, self._cloud_serial, self._source) if cloud else None,
            now=now,
            mqtt_limit=MQTT_LIMIT,
            http_limit=HTTP_LIMITS[self._source.time_key or STREAM_TIME],
        )
        shown = (
            (
                True,
                shown_number(picked[0], self._attr_native_unit_of_measurement),
                picked[1],
            )
            if picked
            else (False, None, None)
        )
        if shown == self._shown:
            return False
        self._shown = shown
        self._attr_available, self._attr_native_value, source = shown
        self._attr_extra_state_attributes = {"source": source}
        return True

    async def async_added_to_hass(self) -> None:
        self.update_from_sources()
        if self._modbus is not None:
            # The context makes the Modbus coordinator read this key.
            self.async_on_remove(
                self._modbus.async_add_listener(self._updated, frozenset({self._key}))
            )
        if self._cloud is not None:
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
