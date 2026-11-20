"""The hybrid entry: Modbus against the fake inverter, the cloud faked."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import Mock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud import coordinator as cloud_module
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi
from tests.fake_inverter import HOST, FakeInverter

type SetUp = Callable[[MockConfigEntry, FakeApi], Awaitable[int]]


@pytest.fixture
async def hybrid_entry(
    hass: HomeAssistant, inverter: FakeInverter
) -> AsyncIterator[MockConfigEntry]:
    inverter.serve_serial(SERIAL, const.SERIAL_NUMBER.address)
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={
            const.CONF_HOST: HOST,
            const.CONF_PORT: inverter.port,
            CONF_EMAIL: "owner@example.com",
            CONF_PASSWORD: "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        },
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def cloud_entry(hass: HomeAssistant) -> AsyncIterator[MockConfigEntry]:
    """Cloud only: no Modbus, so the joined values show the cloud's sources."""
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=CLOUD_SN,
        minor_version=2,
        data={
            CONF_EMAIL: "owner@example.com",
            CONF_PASSWORD: "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        },
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def set_up_hybrid(hass: HomeAssistant) -> SetUp:
    """Set the entry up with *api* as its cloud; return how often reauth started."""

    async def set_up(entry: MockConfigEntry, api: FakeApi) -> int:
        with (
            patch.object(cloud_module, "create_api", Mock(return_value=api)),
            patch.object(MockConfigEntry, "async_start_reauth_if_available") as reauth,
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return reauth.call_count

    return set_up
