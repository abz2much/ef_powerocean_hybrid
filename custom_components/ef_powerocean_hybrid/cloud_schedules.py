"""The scheduled tasks of the EcoFlow app: a switch each and the active one.

Only with the option MQTT. The switches follow the list the system reports;
their name says what the schedule does and changes with it, the entity ID does
not. The schedule switches and the Active schedule sensor of
powerocean_single_phase (v1.0.1), MIT, (c) 2026 niltrip, abz2much.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud.coordinator import CloudCoordinator
from .cloud.mqtt import Schedule
from .cloud.schedules import schedule_attributes, schedule_title
from .const import CONF_MQTT, DOMAIN
from .ids import system_id
from .schedule_services import changes_from_service


@callback
def schedule_switches(
    hass: HomeAssistant,
    entry: ConfigEntry,
    cloud: CloudCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> Callable[[], None] | None:
    """Add a switch per schedule as the list comes, if the MQTT option is on.

    Return what stops following the list. Switched off: remove the switches
    instead of leaving them unavailable.
    """
    registry = er.async_get(hass)
    prefix = f"{system_id(entry)}_schedule_"

    def registered_schedules() -> dict[int, str]:
        """Return the schedule switches in the registry: id -> entity ID."""
        return {
            int(registry_entry.unique_id.removeprefix(prefix)): registry_entry.entity_id
            for registry_entry in er.async_entries_for_config_entry(
                registry, entry.entry_id
            )
            if registry_entry.domain == "switch"
            and registry_entry.unique_id.startswith(prefix)
        }

    if not entry.options.get(CONF_MQTT, False):
        for entity_id in registered_schedules().values():
            registry.async_remove(entity_id)
        return None

    added: set[int] = set()

    @callback
    def follow_the_schedules() -> None:
        """Add a switch for each schedule, remove the ones that are gone."""
        if not cloud.schedules_known:
            return
        ids = set(cloud.schedules)
        if new := sorted(ids - added):
            async_add_entities(
                [CloudSchedule(cloud, entry, schedule_id) for schedule_id in new]
            )
        # Also the switches of an earlier run that are not in the list any more
        registered = registered_schedules()
        for schedule_id in (added | set(registered)) - ids:
            if entity_id := registered.get(schedule_id):
                registry.async_remove(entity_id)
        added.clear()
        added.update(ids)

    follow_the_schedules()
    return cloud.async_add_listener(follow_the_schedules)


@callback
def active_schedule_sensor(
    hass: HomeAssistant,
    entry: ConfigEntry,
    cloud: CloudCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> Callable[[], None] | None:
    """Add the Active schedule sensor once the list is known, if MQTT is on.

    Return what stops waiting for the list. Switched off: remove it instead of
    leaving it unavailable.
    """
    sensor = ActiveSchedule(cloud, entry)
    if not entry.options.get(CONF_MQTT, False):
        registry = er.async_get(hass)
        if entity_id := registry.async_get_entity_id(
            "sensor", DOMAIN, str(sensor.unique_id)
        ):
            registry.async_remove(entity_id)
        return None

    added = False

    @callback
    def add_when_known() -> None:
        # Only when the list is known: by then the device has its name, which
        # the entity ID is made from (not the serial number)
        nonlocal added
        if added or not cloud.schedules_known:
            return
        added = True
        async_add_entities([sensor])

    add_when_known()
    return cloud.async_add_listener(add_when_known)


class CloudSchedule(CoordinatorEntity[CloudCoordinator], SwitchEntity):
    """A scheduled task of the app: on or off.

    The unique ID has the id of the schedule, so the entity ID stays fixed
    while the name follows the schedule.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CloudCoordinator, entry: ConfigEntry, schedule_id: int
    ) -> None:
        super().__init__(coordinator)
        self._schedule_id = schedule_id
        system = system_id(entry)
        self._attr_unique_id = f"{system}_schedule_{schedule_id}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, system)})

    @property
    def suggested_object_id(self) -> str:
        """Return the id of the schedule, so the entity ID does not depend on it."""
        return f"schedule_{self._schedule_id}"

    @property
    def _schedule(self) -> Schedule | None:
        return self.coordinator.schedules.get(self._schedule_id)

    @property
    def name(self) -> str:
        """Return what the schedule does, e.g. "Charge 02:00-06:00 3000 W"."""
        schedule = self._schedule
        return schedule_title(schedule) if schedule else f"Schedule {self._schedule_id}"

    @property
    def available(self) -> bool:
        """Return False without MQTT connection or if the schedule is gone."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self._schedule is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the schedule is switched on."""
        schedule = self._schedule
        return None if schedule is None else schedule.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return id, type, start, end, power, days and whether it is in use."""
        schedule = self._schedule
        return schedule_attributes(schedule) if schedule else {}

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Switch on; raises if the system does not confirm it."""
        await self.coordinator.async_set_schedule_enabled(
            self._schedule_id, enabled=True
        )

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Switch off; raises if the system does not confirm it."""
        await self.coordinator.async_set_schedule_enabled(
            self._schedule_id, enabled=False
        )

    async def async_update_schedule(self, **service_data: Any) -> None:
        """Change the schedule (action update_schedule); what is not given stays."""
        await self.coordinator.async_update_schedule(
            self._schedule_id, **changes_from_service(service_data)
        )

    async def async_delete_schedule(self) -> None:
        """Delete the schedule (action delete_schedule)."""
        await self.coordinator.async_delete_schedule(self._schedule_id)


class ActiveSchedule(CoordinatorEntity[CloudCoordinator], SensorEntity):
    """The scheduled task the system is using now, "none" without one.

    The system switches schedules by itself and reports no change, so this
    follows the regular reading of the list (every minute).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "active_schedule"

    def __init__(self, coordinator: CloudCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        system = system_id(entry)
        self._attr_unique_id = f"{system}_active_schedule"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, system)})

    @property
    def suggested_object_id(self) -> str:
        """Return a fixed id, so the entity ID does not depend on the name."""
        return "active_schedule"

    @property
    def available(self) -> bool:
        """Return False without MQTT connection or before the list was read."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.coordinator.schedules_known
        )

    @property
    def native_value(self) -> str:
        """Return what the active schedule does, e.g. "Charge 02:00-06:00 3000 W"."""
        schedule = self.coordinator.active_schedule
        return schedule_title(schedule) if schedule else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return id, type, start, end, power, days and in_use of the schedule."""
        schedule = self.coordinator.active_schedule
        return schedule_attributes(schedule) if schedule else {}
