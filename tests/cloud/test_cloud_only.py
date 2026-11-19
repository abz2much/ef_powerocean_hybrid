"""An entry with only a cloud connection: a PowerOcean Single Phase without Modbus."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, FakeApi, model86_response
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def cloud_entry(hass: HomeAssistant) -> AsyncIterator[MockConfigEntry]:
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


async def test_a_cloud_only_entry_loads(
    cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    assert cloud_entry.state is ConfigEntryState.LOADED
    assert cloud_entry.runtime_data.modbus is None
    assert cloud_entry.runtime_data.cloud.last_update_success


async def test_it_has_the_cloud_entities_and_no_modbus_ones(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    unique_ids = {
        e.unique_id
        for e in er.async_entries_for_config_entry(
            er.async_get(hass), cloud_entry.entry_id
        )
    }
    assert f"{CLOUD_SN}_cloud_pv1_voltage" in unique_ids
    assert "SN_BP01_pack_soc" in unique_ids
    assert f"{CLOUD_SN}_inverter_temperature" not in unique_ids
    assert not any(uid.endswith("_modbus_control") for uid in unique_ids)


async def test_the_inverter_device_has_a_name_and_model(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    device = dr.async_get(hass).async_get_device_by_identifier(
        (const.DOMAIN, CLOUD_SN), cloud_entry.entry_id
    )
    assert device is not None
    assert device.name == "EcoFlow PowerOcean"
    assert device.model == "PowerOcean Single Phase"


async def test_it_unloads(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    assert await hass.config_entries.async_unload(cloud_entry.entry_id)
    assert cloud_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_work_without_modbus(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    from custom_components.ef_powerocean_hybrid.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    diagnostics = await async_get_config_entry_diagnostics(hass, cloud_entry)

    assert diagnostics["modbus"] is None
    text = str(diagnostics)
    for secret in ("owner@example.com", "secret", CLOUD_SN):
        assert secret not in text, secret


async def test_the_battery_action_is_refused_without_modbus(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    from homeassistant.exceptions import ServiceValidationError

    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (const.DOMAIN, CLOUD_SN), cloud_entry.entry_id
    )
    assert device is not None

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            const.DOMAIN,
            "set_battery_command",
            {"device_id": device.id, "mode": "automatic"},
            blocking=True,
        )


async def test_adding_modbus_keeps_every_cloud_entity_and_adds_new_counters(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    """B6: the cloud's entities keep their IDs; Modbus counters are new entities."""
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))
    registry = er.async_get(hass)
    before = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(registry, cloud_entry.entry_id)
    }
    assert f"{CLOUD_SN}_bat_charged_total" not in before

    inverter.serve_serial(CLOUD_SN, const.SERIAL_NUMBER.address)
    await hass.config_entries.async_unload(cloud_entry.entry_id)
    hass.config_entries.async_update_entry(
        cloud_entry,
        data={
            **cloud_entry.data,
            const.CONF_HOST: HOST,
            const.CONF_PORT: inverter.port,
        },
    )
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    after = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(registry, cloud_entry.entry_id)
    }
    # Unloaded here: the fake inverter waits for the open connection otherwise.
    await hass.config_entries.async_unload(cloud_entry.entry_id)
    await hass.async_block_till_done()
    assert {uid: after.get(uid) for uid in before} == before
    assert f"{CLOUD_SN}_bat_charged_total" in after
