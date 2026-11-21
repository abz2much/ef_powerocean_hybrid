"""Select entities for EF PowerOcean Hybrid."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BATTERY_MODE_SELECT, SCHEDULES_OPTION
from .coordinator import EcoflowConfigEntry, EcoflowCoordinator
from .entity import EcoFlowBaseEntity
from .models import ControlEntityDef, ControlFeature

# One write to the inverter at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - required by the platform API
    entry: EcoflowConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EcoFlow selects from a config entry."""
    coordinator = entry.runtime_data.modbus
    if coordinator is None:
        # Only Modbus has these.
        return

    async_add_entities(
        [EcoFlowBatteryModeSelect(coordinator, entry, BATTERY_MODE_SELECT)]
    )


class EcoFlowBatteryModeSelect(EcoFlowBaseEntity[ControlEntityDef], SelectEntity):
    """What the inverter should be doing.

    The protocol follows one control method at a time, so this is a single choice
    rather than several toggles. Each mode's power and the two state-of-charge
    limits are separate entities that stay editable whatever is selected here.
    """

    def __init__(
        self,
        coordinator: EcoflowCoordinator,
        entry: ConfigEntry,
        definition: ControlEntityDef,
    ) -> None:
        super().__init__(coordinator, entry, definition)
        self._attr_options = [str(feature) for feature in ControlFeature]
        # Only where the app's schedules can be read, so a Modbus-only entry
        # keeps the options it has always had.
        if coordinator.control.schedules_available:
            self._attr_options.append(SCHEDULES_OPTION)
        self._attr_entity_category = definition.entity_category

    @callback
    def _handle_coordinator_update(self) -> None:
        # The logbook then shows the expiry as the cause of the mode change.
        if context := self.coordinator.pop_command_expired_context():
            self.async_set_context(context)
        super()._handle_coordinator_update()

    @property
    def current_option(self) -> str:
        if self.coordinator.control.following_schedules:
            return SCHEDULES_OPTION
        return str(self.coordinator.control.selected_feature)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "status": str(self.coordinator.control.status),
            "commanded_power": self.coordinator.control.power,
        }

    async def async_select_option(self, option: str) -> None:
        if option == SCHEDULES_OPTION:
            await self.coordinator.control.async_follow_schedules()
            return
        await self.coordinator.control.async_select_feature(ControlFeature(option))
