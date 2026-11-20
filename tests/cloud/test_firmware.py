"""Firmware on the device pages: each device keeps its own version."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud_sensor import pack_serials
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response
from tests.fake_inverter import FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# moduleAplSwVer 67178755 in the fixture, 0x04011103.
PACK_FIRMWARE = "4.1.17.3"


def _sw_version(hass: HomeAssistant, identifier: str) -> str | None:
    (device,) = (
        device
        for device in dr.async_get(hass).devices
        if (const.DOMAIN, identifier) in device.identifiers
    )
    return device.sw_version


def _packs(entry: MockConfigEntry) -> list[str]:
    packs = sorted(pack_serials(entry.runtime_data.cloud.data, CLOUD_SN))
    assert packs
    return packs


def _with_pack_firmware(version: int) -> dict[str, Any]:
    """The model-86 response with every pack at *version*.

    Each pack's values are a JSON text inside the report, as the cloud sends them.
    """
    response = copy.deepcopy(model86_response())
    for report in response["data"]["quota"].values():
        if not isinstance(report, dict):
            continue
        for key, box in report.items():
            if isinstance(box, str) and "moduleAplSwVer" in box:
                pack = json.loads(box)
                pack["moduleAplSwVer"] = version
                report[key] = json.dumps(pack)
    return response


async def test_the_packs_show_their_firmware_from_the_cloud(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    """As the cloud integration: moduleAplSwVer on each battery device."""
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    for pack in _packs(hybrid_entry):
        assert _sw_version(hass, pack) == PACK_FIRMWARE


async def test_a_new_pack_firmware_is_shown_without_a_reload(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[model86_response()])
    await set_up_hybrid(hybrid_entry, api)

    api.respond(_with_pack_firmware(0x04011200))
    await hybrid_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()

    for pack in _packs(hybrid_entry):
        assert _sw_version(hass, pack) == "4.1.18.0"


async def test_a_modbus_firmware_change_updates_the_inverter_only(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    """The Modbus firmware is the inverter's; the batteries keep their own."""
    address = const.FIRMWARE_VERSION.address
    inverter.registers[address], inverter.registers[address + 1] = 0x0304, 0x0102
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))
    assert _sw_version(hass, SERIAL) == "1.2.3.4"

    inverter.registers[address] = 0x0400
    await hybrid_entry.runtime_data.modbus.async_read_device_info()
    await hass.async_block_till_done()

    assert _sw_version(hass, SERIAL) == "1.2.4.0"
    for pack in _packs(hybrid_entry):
        assert _sw_version(hass, pack) == PACK_FIRMWARE
