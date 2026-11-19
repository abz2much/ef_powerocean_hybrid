"""Sensor entities for EF PowerOcean Hybrid."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .catalogue import CATALOGUE_86
from .cloud_sensor import CloudCounter, HeldCounter, cloud_sensors
from .const import (
    BATTERY_RESERVE_REGISTER_KEY,
    BATTERY_SOC_KEYS,
    CONF_BATTERY_COUNT,
    CONF_COUNTER_SOURCE,
    CONF_HOST,
    CONF_INVERTER_MODEL,
    CONTROL_STATUS_SENSOR,
    CONTROL_TEST_SENSOR,
    DAILY_ENERGY_SENSORS_DEVICE_RAW,
    EFFECTIVE_RESERVE_SENSOR,
    ENERGY_SENSOR_MAP,
    FAULT_KEYS,
    SENSOR_MAP,
    TOTAL_ENERGY_SENSORS_DEVICE_RAW,
    WRITABLE_NUMBERS_MAP,
)
from .coordinator import EcoflowConfigEntry, EcoflowCoordinator
from .display import VALUE_PRECISION, shown_number
from .energy_processor import DEVICE_TOTAL_KEYS
from .entity import EcoFlowBaseEntity
from .fault_names import async_get_fault_names
from .joined_sensor import JoinedSensor
from .models import EnergySensorDef, InverterModel, SensorDef
from .telemetry import active_fault_codes

# The coordinator polls for every entity, so updates need no limit.
PARALLEL_UPDATES = 0

type SensorValue = StateType | date | datetime | Decimal

_LOGGER = logging.getLogger(__name__)

ACTIVE_FAULTS_KEY: Final = "active_faults"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcoflowConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.modbus
    cloud = entry.runtime_data.cloud
    model = (
        coordinator.inverter_model
        if coordinator is not None
        else entry.options.get(CONF_INVERTER_MODEL)
    )
    # The cloud is read for one model only; its values join the Modbus ones.
    with_cloud = cloud is not None and model == InverterModel.POWEROCEAN_SINGLE_PHASE
    joined = [e for e in CATALOGUE_86 if e.kind == "joined"] if with_cloud else []

    if coordinator is not None:
        _add_modbus_sensors(
            coordinator, entry, async_add_entities, skip={e.key for e in joined}
        )
    elif CONF_HOST in entry.data:
        # Running on the cloud because Modbus did not answer at start: the
        # Modbus counters keep their last value until the entry reloads.
        async_add_entities(
            HeldCounter(entry, definition.key)
            for definition in CATALOGUE_86
            if definition.kind == "counter"
        )
    async_add_entities(
        JoinedSensor(
            entry,
            definition,
            coordinator,
            cloud,
            VALUE_PRECISION.get(definition.description.unit or ""),
        )
        for definition in joined
    )
    if with_cloud and cloud is not None:
        entry.async_on_unload(cloud_sensors(hass, entry, cloud, async_add_entities))
        if entry.data.get(CONF_COUNTER_SOURCE) == "cloud":
            async_add_entities(
                CloudCounter(cloud, entry, definition)
                for definition in CATALOGUE_86
                if definition.kind == "counter"
            )


@callback
def _add_modbus_sensors(
    coordinator: EcoflowCoordinator,
    entry: EcoflowConfigEntry,
    async_add_entities: AddEntitiesCallback,
    skip: set[str],
) -> None:
    """Add the sensors of the inverter's Modbus registers, except those in *skip*."""
    entities: list[EcoflowSensor] = []

    empty_battery_slots = set(
        BATTERY_SOC_KEYS[coordinator.limits[CONF_BATTERY_COUNT] :]
    )
    # A writable number already shows its register's value, so a read-only twin would
    # only be a second row saying the same thing.
    controlled_keys = {
        number_def.read_key
        for number_def in WRITABLE_NUMBERS_MAP
        if coordinator.inverter_model not in number_def.unsupported_models
    }
    # A native Battery Reserve is that register, so the App Backup Reserve would
    # repeat it.
    if coordinator.control.reserve_native:
        controlled_keys.add(BATTERY_RESERVE_REGISTER_KEY)

    for sensor in SENSOR_MAP:
        if (
            sensor.key in skip
            or sensor.key in empty_battery_slots
            or sensor.key in controlled_keys
            or coordinator.inverter_model in sensor.unsupported_models
        ):
            continue
        entity_class = (
            EcoFlowActiveFaultsSensor
            if sensor.key == ACTIVE_FAULTS_KEY
            else EcoflowSensor
        )
        entities.append(entity_class(coordinator, entry, sensor))

    energy_sensors: list[SensorDef | EnergySensorDef] = [
        *ENERGY_SENSOR_MAP,
        *DAILY_ENERGY_SENSORS_DEVICE_RAW,
        *TOTAL_ENERGY_SENSORS_DEVICE_RAW,
    ]
    entities.extend(
        EcoflowSensor(coordinator, entry, sensor) for sensor in energy_sensors
    )

    async_add_entities(
        [
            *entities,
            EcoFlowControlStatusSensor(coordinator, entry),
            EcoFlowEffectiveReserveSensor(coordinator, entry),
            EcoFlowControlTestSensor(coordinator, entry),
        ]
    )


class EcoFlowControlStatusSensor(EcoFlowBaseEntity[SensorDef], SensorEntity):
    """Whether the selected mode is achieving anything, and why not if it isn't.

    The select says what was asked for; this says what the inverter is managing to
    do about it, which is not the same thing on a device that silently ignores a
    target the battery has no headroom for.
    """

    # Don't record these, since they change every time and it fills up the logbook
    _unrecorded_attributes = frozenset({"expires_at"})

    def __init__(self, coordinator: EcoflowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, CONTROL_STATUS_SENSOR)
        self._attr_device_class = CONTROL_STATUS_SENSOR.device_class
        self._attr_options = list(CONTROL_STATUS_SENSOR.options or ())

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        return str(self.coordinator.control.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        control = self.coordinator.control
        return {
            "mode": str(control.selected_feature),
            "guard": str(guard) if (guard := control.active_guard) else None,
            "commanded_power": control.power,
            "control_method": str(control.method),
            "in_control": control.in_control,
            "expires_at": control.expires_at,
        }


class EcoFlowEffectiveReserveSensor(EcoFlowBaseEntity[SensorDef], SensorEntity):
    """The state of charge the battery is held at, as the control loop keeps it."""

    def __init__(self, coordinator: EcoflowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, EFFECTIVE_RESERVE_SENSOR)
        self._attr_native_unit_of_measurement = EFFECTIVE_RESERVE_SENSOR.unit
        self._attr_state_class = EFFECTIVE_RESERVE_SENSOR.state_class
        self._attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        return self.coordinator.control.reserve_in_force[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        source = self.coordinator.control.reserve_in_force[1]
        return {"source": None if source is None else str(source)}


class EcoFlowControlTestSensor(EcoFlowBaseEntity[SensorDef], SensorEntity):
    """Follows the control test the run_control_test action starts."""

    def __init__(self, coordinator: EcoflowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, CONTROL_TEST_SENSOR)
        self._attr_device_class = CONTROL_TEST_SENSOR.device_class
        self._attr_entity_category = CONTROL_TEST_SENSOR.entity_category
        self._attr_options = list(CONTROL_TEST_SENSOR.options or ())

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        return str(self.coordinator.control_test.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.control_test.attributes


class EcoflowSensor(EcoFlowBaseEntity[SensorDef | EnergySensorDef], RestoreSensor):
    def __init__(
        self,
        coordinator: EcoflowCoordinator,
        entry: ConfigEntry,
        definition: SensorDef | EnergySensorDef,
    ) -> None:
        super().__init__(coordinator, entry, definition)
        self._attr_native_unit_of_measurement = self._definition.unit
        self._attr_device_class = self._definition.device_class
        self._attr_state_class = self._definition.state_class
        if isinstance(definition, SensorDef) and definition.options:
            self._attr_options = list(definition.options)
        # Shown while a poll leaves the value out; restored at a restart.
        self._last_written_value: SensorValue = None

        if definition.unit is not None:
            self._attr_suggested_display_precision = VALUE_PRECISION.get(
                definition.unit
            )

        self._attr_entity_category = self._definition.entity_category
        if disabled_models := getattr(self._definition, "disabled_models", ()):
            self._attr_entity_registry_enabled_default = (
                coordinator.inverter_model not in disabled_models
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._last_written_value = self.native_value
        self._async_write_if_changed(
            self._last_written_value, self.extra_state_attributes
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (
            last_value := await self.async_get_last_sensor_data()
        ) and last_value.native_value is not None:
            _LOGGER.debug(
                "Restore sensor %s with value %s",
                self._definition.key,
                last_value.native_value,
            )
            self._last_written_value = last_value.native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Publish the data keys the definition names, such as a raw word."""
        if not isinstance(self._definition, SensorDef):
            return None
        keys = self._definition.attribute_keys
        if not keys:
            return None
        data = self.coordinator.data or {}
        return {key: data.get(key) for key in keys}

    @property
    def available(self) -> bool:
        """Keep coordinator diagnostics available when normal entities are not."""
        if self._definition.key == "coordinator_status":
            return self.coordinator.status is not None
        # A reading the checks show to be wrong, such as a system level of 0 while
        # the batteries are full (#200), is not shown as if it were real.
        if self._definition.key in self.coordinator.control.reading_checks.distrusted:
            return False
        return super().available

    @property
    def native_value(self) -> SensorValue:
        """Return the value from the coordinator, else the last one shown."""
        if self._definition.key == "coordinator_status":
            return self.coordinator.status
        value = (self.coordinator.data or {}).get(self._definition.key)
        key = self._definition.key
        if value is None or (
            key in DEVICE_TOTAL_KEYS and key in self.coordinator.unconfirmed_totals
        ):
            # A first 0 of a lifetime total is not shown: a jump from it to the
            # real total would book the whole lifetime as one hour.
            return self._last_written_value
        if isinstance(value, datetime | str):
            return value
        if self._definition.device_class is SensorDeviceClass.ENUM:
            return str(value)
        return shown_number(value, self._definition.unit)


class EcoFlowActiveFaultsSensor(EcoflowSensor):
    """The fault codes the device reports, each named where the code is known."""

    def __init__(
        self,
        coordinator: EcoflowCoordinator,
        entry: ConfigEntry,
        definition: SensorDef | EnergySensorDef,
    ) -> None:
        super().__init__(coordinator, entry, definition)
        self._fault_names: dict[int, str] = {}

    async def async_added_to_hass(self) -> None:
        self._fault_names = await async_get_fault_names(self.hass)
        await super().async_added_to_hass()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Name each active code, or None for a code nobody has named yet."""
        data = self.coordinator.data or {}
        codes = active_fault_codes(data.get(key) for key in FAULT_KEYS)
        return {
            "faults": [
                {"code": code, "name": self._fault_names.get(code)} for code in codes
            ]
        }
