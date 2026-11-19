"""The cloud's entities of a PowerOcean Single Phase, from the catalogue."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.catalogue import CATALOGUE_86
from custom_components.ef_powerocean_hybrid.cloud.api import EcoflowConnectionError
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import SERIAL, FakeApi, model86_response

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TRANSLATIONS = Path(const.__file__).parent / "translations" / "en.json"


def _entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", const.DOMAIN, unique_id)


def _state(hass: HomeAssistant, unique_id: str) -> Any:
    entity_id = _entity_id(hass, unique_id)
    assert entity_id is not None, unique_id
    state = hass.states.get(entity_id)
    assert state is not None, unique_id
    return state


def _without_pack(response: dict[str, Any], pack: str) -> dict[str, Any]:
    trimmed = copy.deepcopy(response)
    quota = trimmed["data"]["quota"]
    report = next(key for key in quota if key.endswith("_BP_STA_REPORT"))
    quota[report].pop(pack)
    return trimmed


async def test_cloud_entities_join_the_inverter_device(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    entity = er.async_get(hass).async_get(
        _entity_id(hass, f"{SERIAL}_cloud_pv1_voltage") or ""
    )
    modbus = er.async_get(hass).async_get(
        _entity_id(hass, f"{SERIAL}_house_power") or ""
    )
    assert entity is not None
    assert modbus is not None
    assert entity.device_id == modbus.device_id
    device = dr.async_get(hass).async_get(entity.device_id or "")
    assert device is not None
    assert device.identifiers == {(const.DOMAIN, SERIAL)}


async def test_each_pack_gets_a_device_under_the_inverter(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    registry = dr.async_get(hass)
    inverter = registry.async_get_device_by_identifier(
        (const.DOMAIN, SERIAL), hybrid_entry.entry_id
    )
    assert inverter is not None
    for pack in ("SN_BP01", "SN_BP02"):
        device = registry.async_get_device_by_identifier(
            (const.DOMAIN, pack), hybrid_entry.entry_id
        )
        assert device is not None, pack
        assert device.via_device_id == inverter.id
        assert _state(hass, f"{pack}_pack_soc").state == "72"


async def test_values_keep_the_cloud_unit_and_sign(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    assert float(
        _state(hass, f"{SERIAL}_cloud_inverter_ac_power").state
    ) == pytest.approx(437.91104)
    charged = _state(hass, "SN_BP01_pack_charged_total")
    assert charged.state == "1600000"
    assert charged.attributes["unit_of_measurement"] == "Wh"
    assert float(_state(hass, f"{SERIAL}_cloud_pv1_voltage").state) == pytest.approx(
        313.71265
    )


async def test_a_value_sent_as_text_is_a_number(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    assert float(_state(hass, f"{SERIAL}_solar_total_cloud").state) == 7500.0


async def test_an_update_time_is_a_timestamp(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    assert _state(hass, f"{SERIAL}_heartbeat_update_time").state.startswith(
        "2026-11-19T"
    )


async def test_entries_marked_disabled_start_disabled(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    entity_id = _entity_id(hass, f"{SERIAL}_cloud_feed_mode")
    assert entity_id is not None
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_a_pack_missing_from_a_response_goes_unavailable(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    response = model86_response()
    api = FakeApi(fetch=[response])
    await set_up_hybrid(hybrid_entry, api)

    api.respond(_without_pack(response, "SN_BP02"))
    await hybrid_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, "SN_BP02_pack_soc").state == "unavailable"
    assert _state(hass, "SN_BP01_pack_soc").state == "72"


async def test_packs_first_seen_after_setup_are_added(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[EcoflowConnectionError("down")])
    await set_up_hybrid(hybrid_entry, api)
    assert _entity_id(hass, "SN_BP01_pack_soc") is None

    api.respond(model86_response())
    await hybrid_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, "SN_BP01_pack_soc").state == "72"


async def test_the_cloud_entities_are_unavailable_while_the_cloud_is_down(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[EcoflowConnectionError("down")]))

    assert _state(hass, f"{SERIAL}_cloud_pv1_voltage").state == "unavailable"


async def test_another_model_gets_no_cloud_entities(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    hass.config_entries.async_update_entry(
        hybrid_entry, options={const.CONF_INVERTER_MODEL: "powerocean_three_phase"}
    )
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    assert _entity_id(hass, f"{SERIAL}_cloud_pv1_voltage") is None


def test_every_catalogue_entity_has_a_name() -> None:
    names = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))["entity"]["sensor"]
    missing = [e.key for e in CATALOGUE_86 if "name" not in names.get(e.key, {})]
    assert missing == []
