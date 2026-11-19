"""A fresh install whose first total reads 0 never shows that 0 (upstream bug #2).

Shown, the 0 would later jump to the lifetime total, and the Energy dashboard
would book the whole lifetime as one hour.
"""

from __future__ import annotations

import struct

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.ef_powerocean_hybrid import const
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SERIAL = "HJ31ZAS2TEST0001"
UNIQUE_ID = f"{SERIAL}_bat_charged_total"


def _serve_float(inverter: FakeInverter, address: int, value: float) -> None:
    low, high = struct.unpack("<HH", struct.pack("<f", value))
    inverter.registers[address] = low
    inverter.registers[address + 1] = high


async def _first_state(
    hass: HomeAssistant, inverter: FakeInverter, *, restored: float | None
) -> str:
    inverter.serve_serial(SERIAL, const.SERIAL_NUMBER.address)
    _serve_float(inverter, const.REGISTERS_BY_KEY["bat_charged_total"].address, 0.0)
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={const.CONF_HOST: HOST, const.CONF_PORT: inverter.port},
    )
    entry.add_to_hass(hass)
    if restored is not None:
        entity = er.async_get(hass).async_get_or_create(
            "sensor",
            const.DOMAIN,
            UNIQUE_ID,
            config_entry=entry,
            suggested_object_id="ecoflow_powerocean_battery_charged_total",
        )
        mock_restore_cache_with_extra_data(
            hass,
            (
                (
                    State(entity.entity_id, str(restored)),
                    {"native_value": restored, "native_unit_of_measurement": "kWh"},
                ),
            ),
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", const.DOMAIN, UNIQUE_ID
    )
    state = hass.states.get(entity_id or "")
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert state is not None
    return state.state


async def test_a_first_zero_keeps_the_restored_value(
    hass: HomeAssistant, inverter: FakeInverter
) -> None:
    assert float(await _first_state(hass, inverter, restored=3268.69)) == 3268.69


async def test_a_first_zero_without_history_is_unknown(
    hass: HomeAssistant, inverter: FakeInverter
) -> None:
    assert await _first_state(hass, inverter, restored=None) == "unknown"
