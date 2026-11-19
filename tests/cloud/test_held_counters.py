"""B5 with A7: Modbus counters keep their last value.

While the entry runs on the cloud.
"""

from __future__ import annotations

import socket
import struct
from typing import Any

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.ef_powerocean_hybrid import const
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "socket_enabled")

CHARGED = f"{SERIAL}_bat_charged_total"
CHARGED_REGISTER = const.REGISTERS_BY_KEY["bat_charged_total"].address


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _serve_float(inverter: FakeInverter, address: int, value: float) -> None:
    """Write a FLOAT register the way the device sends it, low word first."""
    low, high = struct.unpack("<HH", struct.pack("<f", value))
    inverter.registers[address] = low
    inverter.registers[address + 1] = high


def _entry(hass: HomeAssistant, port: int) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={
            const.CONF_HOST: HOST,
            const.CONF_PORT: port,
            CONF_EMAIL: "owner@example.com",
            CONF_PASSWORD: "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        },
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    return entry


def _state(hass: HomeAssistant, unique_id: str) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", const.DOMAIN, unique_id
    )
    assert entity_id is not None, unique_id
    state = hass.states.get(entity_id)
    assert state is not None, unique_id
    return state


async def test_a_held_counter_shows_its_last_value_while_running_on_the_cloud(
    hass: HomeAssistant, set_up_hybrid: SetUp
) -> None:
    entry = _entry(hass, _closed_port())
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        const.DOMAIN,
        CHARGED,
        config_entry=entry,
        suggested_object_id="ecoflow_powerocean_battery_charged_total",
    )
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity.entity_id, "3268.69"),
                {"native_value": 3268.69, "native_unit_of_measurement": "kWh"},
            ),
        ),
    )

    await set_up_hybrid(entry, FakeApi(fetch=[model86_response()]))
    state = _state(hass, CHARGED)
    modbus = entry.runtime_data.modbus
    await hass.config_entries.async_unload(entry.entry_id)

    assert modbus is None
    assert float(state.state) == 3268.69
    assert state.attributes["source"] == "modbus (held)"
    assert state.attributes["state_class"] == "total_increasing"


async def test_a_bogus_first_zero_after_modbus_returns_is_not_a_drop(
    hass: HomeAssistant, set_up_hybrid: SetUp, inverter: FakeInverter
) -> None:
    """Modbus runs, the next start finds it down, then it returns reading 0."""
    inverter.serve_serial(SERIAL, const.SERIAL_NUMBER.address)
    _serve_float(inverter, CHARGED_REGISTER, 3268.69)
    entry = _entry(hass, inverter.port)

    seen: list[Any] = []

    async def run(port: int) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, const.CONF_PORT: port}
        )
        await set_up_hybrid(entry, FakeApi(fetch=[model86_response()]))
        seen.append(_state(hass, CHARGED).state)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    await run(inverter.port)
    await run(_closed_port())
    _serve_float(inverter, CHARGED_REGISTER, 0.0)
    await run(inverter.port)

    assert [float(value) for value in seen] == [3268.69, 3268.69, 3268.69]


async def test_modbus_counters_are_held_on_an_entry_counting_from_the_cloud(
    hass: HomeAssistant, set_up_hybrid: SetUp
) -> None:
    """Set up through the cloud, given Modbus later, then started with Modbus down."""
    entry = _entry(hass, _closed_port())
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, const.CONF_COUNTER_SOURCE: "cloud"}
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        const.DOMAIN,
        CHARGED,
        config_entry=entry,
        suggested_object_id="ecoflow_powerocean_battery_charged_total",
    )
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity.entity_id, "3268.69"),
                {"native_value": 3268.69, "native_unit_of_measurement": "kWh"},
            ),
        ),
    )

    await set_up_hybrid(entry, FakeApi(fetch=[model86_response()]))
    state = _state(hass, CHARGED)
    await hass.config_entries.async_unload(entry.entry_id)

    assert float(state.state) == 3268.69
    assert state.attributes["source"] == "modbus (held)"
