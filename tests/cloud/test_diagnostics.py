"""Diagnostics never show the account, the serials or the inverter's address."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud.anonymize import Anonymizer
from custom_components.ef_powerocean_hybrid.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response
from tests.cloud.schedule_fakes import COORDINATOR
from tests.cloud.test_mqtt_link import MODULES, FakeMqtt
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SECRETS = ("owner@example.com", "secret", CLOUD_SN, SERIAL, "127.0.0.1")


async def test_a_hybrid_entry_hides_the_account_serials_and_address(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    text = json.dumps(
        await async_get_config_entry_diagnostics(hass, hybrid_entry), default=str
    )

    for secret in SECRETS:
        assert secret not in text, secret


# Task 7: the cloud part, as powerocean_single_phase's diagnostics (v1.0.1).


async def test_a_modbus_only_entry_has_no_cloud_part(
    hass: HomeAssistant, inverter: FakeInverter
) -> None:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        data={const.CONF_HOST: HOST, const.CONF_PORT: inverter.port},
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["cloud"] is None
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_cloud_part_without_mqtt(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    cloud = (await async_get_config_entry_diagnostics(hass, cloud_entry))["cloud"]

    assert cloud["coordinator"] == {
        "last_update_success": True,
        "last_exception": None,
        "update_interval": 10.0,
        "variant": "86",
        "mqtt": False,
    }
    assert cloud["modules"] == {}
    # Serials as placeholders, so the response can become a test fixture.
    assert cloud["api_response"] == Anonymizer(CLOUD_SN).anonymize(model86_response())


async def test_the_cloud_part_shows_mqtt_and_the_module_firmware(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    hass.config_entries.async_update_entry(
        cloud_entry, options={**cloud_entry.options, const.CONF_MQTT: True}
    )
    FakeMqtt.instances.clear()
    with patch(f"{COORDINATOR}.PowerOceanMqtt", FakeMqtt):
        await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))
        (client,) = FakeMqtt.instances
        await hass.async_add_executor_job(client.callbacks.on_modules, MODULES)
        await hass.async_block_till_done()

        cloud = (await async_get_config_entry_diagnostics(hass, cloud_entry))["cloud"]

    assert cloud["coordinator"]["mqtt"] is True
    # The EMS firmware is shown here only, not on a device (owner, Task 2).
    assert cloud["modules"] == {
        address: {
            "type": module.module_type,
            "firmware": module.firmware,
            "loader": module.loader,
        }
        for address, module in sorted(MODULES.items())
    }
    assert cloud["modules"]


async def test_the_cloud_response_shows_no_serial(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    pack = "HJ32TESTPACK0001"
    response = json.loads(json.dumps(model86_response()).replace("SN_BP01", pack))
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[response]))

    cloud = (await async_get_config_entry_diagnostics(hass, cloud_entry))["cloud"]

    assert pack in json.dumps(response)
    assert pack not in json.dumps(cloud["api_response"])
