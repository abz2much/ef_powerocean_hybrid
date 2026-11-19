"""B5, B6: energy counters stay on one source, fixed when the entry is first set up."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud.api import EcoflowConnectionError
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

CHARGED = f"{CLOUD_SN}_bat_charged_total_cloud"
DISCHARGED = f"{CLOUD_SN}_bat_discharged_total_cloud"


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


def _entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", const.DOMAIN, unique_id)


def _state(hass: HomeAssistant, unique_id: str) -> State:
    entity_id = _entity_id(hass, unique_id)
    assert entity_id is not None, unique_id
    state = hass.states.get(entity_id)
    assert state is not None, unique_id
    return state


async def test_a_cloud_only_entry_counts_from_the_cloud(
    cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    assert cloud_entry.data[const.CONF_COUNTER_SOURCE] == "cloud"


async def test_a_modbus_entry_counts_from_modbus(
    hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    assert hybrid_entry.data[const.CONF_COUNTER_SOURCE] == "modbus"


async def test_the_cloud_counters_of_a_cloud_only_entry(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    charged = _state(hass, CHARGED)
    # The fixture's 3,300,000 Wh, in kWh.
    assert float(charged.state) == 3300.0
    assert charged.attributes["unit_of_measurement"] == "kWh"
    assert charged.attributes["state_class"] == "total_increasing"
    assert charged.attributes["friendly_name"].endswith("Battery charged total (cloud)")
    assert float(_state(hass, DISCHARGED).state) == 3100.0


async def test_a_modbus_entry_has_no_cloud_counters(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    assert _entity_id(hass, f"{SERIAL}_bat_charged_total_cloud") is None
    assert _entity_id(hass, f"{SERIAL}_bat_charged_total") is not None


async def test_a_cloud_counter_keeps_its_last_value_while_the_cloud_is_down(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[model86_response()])
    await set_up_hybrid(cloud_entry, api)

    api.respond(EcoflowConnectionError("down"))
    await cloud_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()

    assert float(_state(hass, CHARGED).state) == 3300.0


async def test_a_cloud_counter_keeps_its_last_value_when_the_value_goes_missing(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[model86_response()])
    await set_up_hybrid(cloud_entry, api)

    response: dict[str, Any] = copy.deepcopy(model86_response())
    response["data"]["quota"]["JTS1_EMS_CHANGE_REPORT"].pop("bpTotalChgEnergy")
    api.respond(response)
    await cloud_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()

    assert float(_state(hass, CHARGED).state) == 3300.0


async def test_a_cloud_counter_restores_its_value_with_the_cloud_down_at_start(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    entity_id = "sensor.ecoflow_powerocean_battery_charged_total_cloud"
    er.async_get(hass).async_get_or_create(
        "sensor",
        const.DOMAIN,
        CHARGED,
        config_entry=cloud_entry,
        suggested_object_id="ecoflow_powerocean_battery_charged_total_cloud",
    )
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity_id, "3250.0"),
                {"native_value": 3250.0, "native_unit_of_measurement": "kWh"},
            ),
        ),
    )

    await set_up_hybrid(cloud_entry, FakeApi(fetch=[EcoflowConnectionError("down")]))

    assert float(_state(hass, CHARGED).state) == 3250.0


async def test_adding_modbus_keeps_the_cloud_counters_and_adds_modbus_ones(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    """B6: the cloud counters keep their source; Modbus counters are new entities."""
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))
    cloud_counter = _entity_id(hass, CHARGED)

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

    source = cloud_entry.data[const.CONF_COUNTER_SOURCE]
    cloud_state = float(_state(hass, CHARGED).state)
    modbus_counter = _entity_id(hass, f"{CLOUD_SN}_bat_charged_total")
    # Unloaded before asserting: the fake inverter waits for an open connection.
    await hass.config_entries.async_unload(cloud_entry.entry_id)
    await hass.async_block_till_done()

    assert source == "cloud"
    assert _entity_id(hass, CHARGED) == cloud_counter
    assert cloud_state == 3300.0
    assert modbus_counter is not None


def _charged_wh(wh: int) -> dict[str, Any]:
    response: dict[str, Any] = copy.deepcopy(model86_response())
    response["data"]["quota"]["JTS1_EMS_CHANGE_REPORT"]["bpTotalChgEnergy"] = wh
    return response


@pytest.mark.parametrize(
    ("wh", "shown"),
    (
        (0, 3300.0),
        (3_299_600, 3300.0),
        (3_300_500, 3300.5),
    ),
    ids=("a brief 0", "a small drop of 0.4 kWh", "a real rise"),
)
async def test_a_cloud_counter_never_goes_down(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
    wh: int,
    shown: float,
) -> None:
    """A drop on a total_increasing sensor reads as a meter reset, and the
    Energy dashboard would count the whole lifetime again."""
    api = FakeApi(fetch=[_charged_wh(3_300_000)])
    await set_up_hybrid(cloud_entry, api)

    # Time for a real rise to be believable.
    freezer.tick(timedelta(minutes=10))
    api.respond(_charged_wh(wh))
    await cloud_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()

    assert float(_state(hass, CHARGED).state) == shown


async def test_a_cloud_counter_recovers_after_a_bad_reading(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    api = FakeApi(fetch=[_charged_wh(3_300_000)])
    await set_up_hybrid(cloud_entry, api)
    refresh = cloud_entry.runtime_data.cloud.async_refresh

    shown = []
    for wh in (0, 3_299_600, 3_300_000, 3_300_200):
        freezer.tick(timedelta(minutes=10))
        api.respond(_charged_wh(wh))
        await refresh()
        await hass.async_block_till_done()
        shown.append(float(_state(hass, CHARGED).state))

    assert shown == [3300.0, 3300.0, 3300.0, 3300.2]


async def test_a_one_off_spike_is_not_booked(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Booked, a spike would hold the counter there for good under "never down"."""
    api = FakeApi(fetch=[_charged_wh(3_300_000)])
    await set_up_hybrid(cloud_entry, api)
    refresh = cloud_entry.runtime_data.cloud.async_refresh

    shown = []
    # One minute apart: up to 10 kW, so 0.1 kWh a minute is believable.
    for wh in (9_900_000, 3_300_100, 3_300_200):
        freezer.tick(timedelta(minutes=1))
        api.respond(_charged_wh(wh))
        await refresh()
        await hass.async_block_till_done()
        shown.append(float(_state(hass, CHARGED).state))

    assert shown == [3300.0, 3300.1, 3300.2]


async def test_a_believable_rise_after_a_long_cloud_outage_is_taken(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    api = FakeApi(fetch=[_charged_wh(3_300_000)])
    await set_up_hybrid(cloud_entry, api)

    freezer.tick(timedelta(hours=10))
    api.respond(_charged_wh(3_350_000))
    await cloud_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()

    assert float(_state(hass, CHARGED).state) == 3350.0
