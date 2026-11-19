"""The cloud coordinator: never takes the entry, or the Modbus side, down."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud import coordinator as cloud_module
from custom_components.ef_powerocean_hybrid.cloud.api import (
    AuthenticationError,
    EcoflowApiError,
    EcoflowConnectionError,
)
from custom_components.ef_powerocean_hybrid.cloud.coordinator import CloudCoordinator
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response
from tests.fake_inverter import HOST, FakeInverter


def _coordinator(hass: HomeAssistant, api: FakeApi) -> CloudCoordinator:
    entry = MockConfigEntry(domain=const.DOMAIN, data={})
    entry.add_to_hass(hass)
    return CloudCoordinator(hass, entry, api)


async def test_values_come_from_the_parser(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, FakeApi(fetch=[model86_response()]))

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert f"{CLOUD_SN}_sysLoadPwr" in coordinator.data


async def test_it_logs_in_once_before_the_first_fetch(hass: HomeAssistant) -> None:
    api = FakeApi(fetch=[model86_response()])
    coordinator = _coordinator(hass, api)

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert api.logins == 1


async def test_a_rejected_request_logs_in_again_once(hass: HomeAssistant) -> None:
    api = FakeApi(fetch=[EcoflowApiError("token expired"), model86_response()])
    coordinator = _coordinator(hass, api)

    await coordinator.async_refresh()

    assert api.logins == 2
    assert coordinator.last_update_success


async def test_a_network_error_fails_only_this_update(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, FakeApi(fetch=[EcoflowConnectionError("down")]))

    await coordinator.async_refresh()

    assert not coordinator.last_update_success


async def test_a_rejected_login_asks_to_sign_in_again(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, FakeApi(login=AuthenticationError("bad password")))

    with patch.object(MockConfigEntry, "async_start_reauth_if_available") as reauth:
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    reauth.assert_called_once()


async def test_a_login_that_fails_otherwise_fails_only_this_update(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, FakeApi(login=EcoflowApiError("server error")))

    with patch.object(MockConfigEntry, "async_start_reauth_if_available") as reauth:
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    reauth.assert_not_called()


async def test_a_fetch_rejected_again_after_logging_in_fails_the_update(
    hass: HomeAssistant,
) -> None:
    api = FakeApi(fetch=[EcoflowApiError("rejected"), EcoflowApiError("rejected")])
    coordinator = _coordinator(hass, api)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert api.logins == 2


async def test_an_unreadable_response_fails_the_update(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, FakeApi(fetch=[{"data": {"quota": "broken"}}]))

    await coordinator.async_refresh()

    assert not coordinator.last_update_success


async def test_the_api_uses_the_entry_credentials(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        data={
            CONF_EMAIL: "owner@example.com",
            CONF_PASSWORD: "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        },
    )

    api = cloud_module.create_api(hass, entry)

    assert (api.sn, api.variant) == (CLOUD_SN, "86")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_entry_loads_with_the_cloud_down(
    hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[EcoflowConnectionError("down")]))

    assert hybrid_entry.state is ConfigEntryState.LOADED
    assert hybrid_entry.runtime_data.modbus.last_update_success
    assert not hybrid_entry.runtime_data.cloud.last_update_success


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_rejected_cloud_login_keeps_modbus_running(
    hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    reauth_calls = await set_up_hybrid(
        hybrid_entry, FakeApi(login=AuthenticationError("bad password"))
    )

    assert hybrid_entry.state is ConfigEntryState.LOADED
    assert reauth_calls == 1
    assert hybrid_entry.runtime_data.modbus.last_update_success
    await hybrid_entry.runtime_data.modbus.async_refresh()
    assert hybrid_entry.runtime_data.modbus.last_update_success


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_cloud_values_are_read_at_setup(
    hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    assert f"{CLOUD_SN}_sysLoadPwr" in hybrid_entry.runtime_data.cloud.data


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_modbus_only_entry_has_no_cloud(
    hass: HomeAssistant, inverter: FakeInverter
) -> None:
    inverter.serve_serial(SERIAL, const.SERIAL_NUMBER.address)
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={const.CONF_HOST: HOST, const.CONF_PORT: inverter.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.cloud is None
    await hass.config_entries.async_unload(entry.entry_id)
