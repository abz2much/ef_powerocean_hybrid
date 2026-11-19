"""Adding the cloud to a running Modbus entry keeps everything the owner set up."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import config_flow, const
from custom_components.ef_powerocean_hybrid.cloud import coordinator as cloud_module
from tests.cloud.fakes import SERIAL, FakeApi, model86_response
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

LOGIN = {"email": "owner@example.com", "password": "secret"}


async def test_adding_the_cloud_keeps_every_entity_id_label_and_area(
    hass: HomeAssistant, inverter: FakeInverter
) -> None:
    inverter.serve_serial(SERIAL, const.SERIAL_NUMBER.address)
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title=f"EcoFlow PowerOcean ({HOST})",
        unique_id=SERIAL,
        minor_version=2,
        data={const.CONF_HOST: HOST, const.CONF_PORT: inverter.port},
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # The owner's cut-over: renamed entity IDs, labels and the device's area.
    entities, devices = er.async_get(hass), dr.async_get(hass)
    house = entities.async_get_entity_id(
        "sensor", const.DOMAIN, f"{SERIAL}_house_power"
    )
    assert house is not None
    entities.async_update_entity(
        house, new_entity_id="sensor.solar_inverter_sysloadpwr", labels={"energy"}
    )
    device = devices.async_get_device_by_identifier(
        (const.DOMAIN, SERIAL), entry.entry_id
    )
    assert device is not None
    devices.async_update_device(device.id, area_id="network_room")
    before = {
        e.unique_id: (e.entity_id, e.labels, e.area_id)
        for e in er.async_entries_for_config_entry(entities, entry.entry_id)
    }

    with (
        patch.object(
            config_flow, "async_check_cloud", new=AsyncMock(return_value=None)
        ),
        patch.object(cloud_module, "create_api_for", new=Mock()),
        patch.object(
            cloud_module,
            "create_api",
            Mock(return_value=FakeApi(fetch=[model86_response()])),
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_cloud"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], LOGIN
        )
        await hass.async_block_till_done()

    after = {
        e.unique_id: (e.entity_id, e.labels, e.area_id)
        for e in er.async_entries_for_config_entry(entities, entry.entry_id)
    }
    kept_device = devices.async_get(device.id)
    cloud_loaded = entry.runtime_data.cloud is not None
    # Still fed by the integration, not an orphan left in the registry.
    live = hass.states.get("sensor.solar_inverter_sysloadpwr")
    # A second registration of an entity would get its entity ID plus "_2".
    duplicates = {f"{entity_id}_2" for entity_id, _, _ in before.values()} & {
        e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)
    }
    # Unloaded before asserting: the fake inverter waits for an open connection.
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert cloud_loaded
    assert {uid: after.get(uid) for uid in before} == before
    assert live is not None
    assert live.state != "unavailable"
    assert duplicates == set()
    assert after[f"{SERIAL}_house_power"][0] == "sensor.solar_inverter_sysloadpwr"
    assert f"{SERIAL}_cloud_pv1_voltage" in after
    assert kept_device is not None
    assert kept_device.area_id == "network_room"
