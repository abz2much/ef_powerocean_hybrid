"""A7: Modbus down at start: the cloud keeps the entry up until Modbus answers."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ef_powerocean_hybrid import config_flow, const
from custom_components.ef_powerocean_hybrid import modbus as modbus_module
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response
from tests.fake_inverter import HOST

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "socket_enabled")

RETRY = timedelta(seconds=31)


def _closed_port() -> int:
    """Return a port nothing listens on."""
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _entry(*, cloud: bool) -> MockConfigEntry:
    data: dict[str, object] = {const.CONF_HOST: HOST, const.CONF_PORT: _closed_port()}
    if cloud:
        data |= {
            CONF_EMAIL: "owner@example.com",
            CONF_PASSWORD: "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        }
    return MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data=data,
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )


@pytest.fixture
async def down_entry(hass: HomeAssistant) -> AsyncIterator[MockConfigEntry]:
    entry = _entry(cloud=True)
    entry.add_to_hass(hass)
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_the_entry_loads_on_the_cloud_with_modbus_down(
    down_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(down_entry, FakeApi(fetch=[model86_response()]))

    assert down_entry.state is ConfigEntryState.LOADED
    assert down_entry.runtime_data.modbus is None
    assert down_entry.runtime_data.cloud.last_update_success


async def test_the_cloud_values_are_shown_with_modbus_down(
    hass: HomeAssistant, down_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    from homeassistant.helpers import entity_registry as er

    await set_up_hybrid(down_entry, FakeApi(fetch=[model86_response()]))

    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", const.DOMAIN, f"{SERIAL}_cloud_pv1_voltage"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "unavailable"


async def test_the_failed_modbus_start_leaves_no_client_open(
    down_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    with patch.object(
        modbus_module.ModbusClient,
        "async_close",
        autospec=True,
        side_effect=modbus_module.ModbusClient.async_close,
    ) as close:
        await set_up_hybrid(down_entry, FakeApi(fetch=[model86_response()]))

    assert close.call_count == 1


async def test_a_modbus_only_entry_still_retries_its_setup(hass: HomeAssistant) -> None:
    entry = _entry(cloud=False)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_the_entry_reloads_once_modbus_answers(
    hass: HomeAssistant, down_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(down_entry, FakeApi(fetch=[model86_response()]))

    with (
        patch.object(
            config_flow,
            "async_read_device_settings",
            new=AsyncMock(return_value={config_flow.DEVICE_SERIAL_NUMBER: SERIAL}),
        ),
        patch.object(hass.config_entries, "async_schedule_reload", Mock()) as reload,
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + RETRY)
        await hass.async_block_till_done()

    reload.assert_called_once_with(down_entry.entry_id)


async def test_no_reload_while_modbus_is_still_down(
    hass: HomeAssistant, down_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(down_entry, FakeApi(fetch=[model86_response()]))

    with (
        patch.object(
            config_flow, "async_read_device_settings", new=AsyncMock(return_value=None)
        ) as probe,
        patch.object(hass.config_entries, "async_schedule_reload", Mock()) as reload,
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + RETRY)
        await hass.async_block_till_done()

    probe.assert_awaited_once()
    reload.assert_not_called()


async def test_the_watch_stops_when_the_entry_unloads(
    hass: HomeAssistant, down_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(down_entry, FakeApi(fetch=[model86_response()]))
    assert await hass.config_entries.async_unload(down_entry.entry_id)

    with patch.object(
        config_flow, "async_read_device_settings", new=AsyncMock(return_value=None)
    ) as probe:
        async_fire_time_changed(hass, dt_util.utcnow() + RETRY)
        await hass.async_block_till_done()

    probe.assert_not_awaited()


@pytest.mark.parametrize(
    "settings",
    (
        {},
        {config_flow.DEVICE_SERIAL_NUMBER: "HJ31ZAS2TEST0009"},
    ),
    ids=("nothing read", "another inverter"),
)
async def test_no_reload_unless_this_inverter_answers(
    hass: HomeAssistant,
    down_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    settings: dict[str, str],
) -> None:
    """A connection alone, or another device at the address, would reload forever."""
    await set_up_hybrid(down_entry, FakeApi(fetch=[model86_response()]))

    with (
        patch.object(
            config_flow,
            "async_read_device_settings",
            new=AsyncMock(return_value=settings),
        ),
        patch.object(hass.config_entries, "async_schedule_reload", Mock()) as reload,
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + RETRY)
        await hass.async_block_till_done()

    reload.assert_not_called()
