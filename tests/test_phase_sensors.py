"""Sensors that read 0 on a model are not created, or start disabled."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const, sensor
from custom_components.ef_powerocean_hybrid.models import (
    DeviceIdentity,
    InverterModel,
)

SINGLE_PHASE = (
    InverterModel.POWEROCEAN_SINGLE_PHASE,
    InverterModel.OCEAN_2_SINGLE_PHASE,
    InverterModel.OCEAN_2_PLUS_SINGLE_PHASE,
)
L2_L3_KEYS = {
    f"{name}_l{phase}"
    for name in ("voltage", "current", "grid_voltage", "grid_current")
    for phase in (2, 3)
}
PV3_KEYS = {"pv3_voltage", "pv3_current", "pv3_power"}


async def sensors_of(hass, model: InverterModel) -> dict:
    coordinator = SimpleNamespace(
        control=SimpleNamespace(
            reserve_native=False, reserve_in_force=(None, None), status=None
        ),
        data={},
        inverter_model=model,
        identity=DeviceIdentity(),
        limits={const.CONF_BATTERY_COUNT: 1},
        connected=True,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=const.DOMAIN, entry_id=f"entry_{model}")
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator
    added: list = []
    await sensor.async_setup_entry(hass, entry, cast(AddEntitiesCallback, added.extend))
    return {entity._definition.key: entity for entity in added}


@pytest.mark.parametrize("model", SINGLE_PHASE)
async def test_a_single_phase_model_has_no_l2_or_l3_sensors(hass, model) -> None:
    sensors = await sensors_of(hass, model)

    assert "voltage_l1" in sensors
    assert "grid_voltage_l1" in sensors
    assert not L2_L3_KEYS & sensors.keys()


@pytest.mark.parametrize(
    "model", [model for model in InverterModel if model not in SINGLE_PHASE]
)
async def test_other_models_keep_their_l2_and_l3_sensors(hass, model) -> None:
    sensors = await sensors_of(hass, model)

    assert sensors.keys() >= L2_L3_KEYS


async def test_pv3_starts_disabled_on_the_powerocean_single_phase(hass) -> None:
    sensors = await sensors_of(hass, InverterModel.POWEROCEAN_SINGLE_PHASE)

    for key in PV3_KEYS:
        assert sensors[key].entity_registry_enabled_default is False, key


@pytest.mark.parametrize(
    "model",
    [
        model
        for model in InverterModel
        if model is not InverterModel.POWEROCEAN_SINGLE_PHASE
    ],
)
async def test_pv3_starts_enabled_on_other_models(hass, model) -> None:
    sensors = await sensors_of(hass, model)

    for key in PV3_KEYS:
        assert sensors[key].entity_registry_enabled_default is True, key
