"""Live power (option live_power), as powerocean_single_phase runs it.

Ported from its tests/integration/test_mqtt.py (live power). There the stream
wins over the older HTTP values for 30 s (ENERGY_STREAM_HOLD); here the joined
values take it as their MQTT source, which counts for the same 30 s.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ef_powerocean_hybrid import const
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, FakeApi, model86_response
from tests.cloud.test_mqtt_link import FakeMqtt

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# An energy stream report, as mqtt.energy_stream returns it
STREAM = {
    "sysLoadPwr": 1760.0,
    "sysGridPwr": 10.0,
    "mpptPwr": 1110.0,
    "bpPwr": -640.0,
    "pv1Pwr": 760.0,
    "pv2Pwr": 340.0,
    "bpSoc": 40,
}
# The joined values the stream feeds, from the catalogue's cloud keys
FED = {"house_power": 1760, "battery_power": -640, "grid_power": 10}


class StreamMqtt(FakeMqtt):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.energy_stream_requests = 0

    def request_energy_stream(self) -> None:
        self.energy_stream_requests += 1


@pytest.fixture(autouse=True)
def fake_mqtt() -> Iterator[None]:
    FakeMqtt.instances.clear()
    with patch(
        "custom_components.ef_powerocean_hybrid.cloud.coordinator.PowerOceanMqtt",
        StreamMqtt,
    ):
        yield


def _at(time: str) -> dict[str, Any]:
    """The model-86 response with its energy stream reported at *time*."""
    response = copy.deepcopy(model86_response())
    response["data"]["quota"]["JTS1_ENERGY_STREAM_REPORT"]["updateTime"] = time
    return response


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    set_up: SetUp,
    *,
    mqtt: bool,
    live_power: bool | None,
    api: FakeApi | None = None,
) -> FakeApi:
    options: dict[str, Any] = {**entry.options, const.CONF_MQTT: mqtt}
    if live_power is not None:
        options[const.CONF_LIVE_POWER] = live_power
    hass.config_entries.async_update_entry(entry, options=options)
    api = api or FakeApi(fetch=[_at("2026-11-19 20:00:00")])
    await set_up(entry, api)
    return api


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _client() -> StreamMqtt:
    (client,) = FakeMqtt.instances
    assert isinstance(client, StreamMqtt)
    return client


async def _stream(hass: HomeAssistant) -> None:
    (client,) = FakeMqtt.instances
    # paho calls back from its own thread
    await hass.async_add_executor_job(client.callbacks.on_energy_stream, STREAM)
    await hass.async_block_till_done()


async def _poll(
    hass: HomeAssistant, entry: MockConfigEntry, api: FakeApi, time: str
) -> None:
    """An HTTP poll whose stream report time changed: its values count as fresh."""
    api.respond(_at(time))
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, key: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", const.DOMAIN, f"{CLOUD_SN}_{key}"
    )
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None, key
    return state


def _shown(hass: HomeAssistant) -> dict[str, tuple[float, str]]:
    return {
        key: (float(_state(hass, key).state), _state(hass, key).attributes["source"])
        for key in FED
    }


@pytest.mark.parametrize("live_power", (None, False))
async def test_live_power_off_by_default(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
    live_power: bool | None,
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=live_power)
    client = _client()

    await _tick(hass, freezer, 60)

    assert client.energy_stream_requests == 0


async def test_live_power_needs_mqtt(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid, mqtt=False, live_power=True)

    assert FakeMqtt.instances == []


async def test_live_power_requests_the_stream_every_20_seconds(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)
    client = _client()
    # Right after connecting
    assert client.energy_stream_requests == 1

    await _tick(hass, freezer, 20)
    assert client.energy_stream_requests == 2

    # Unloaded: no more requests
    assert await hass.config_entries.async_unload(cloud_entry.entry_id)
    await _tick(hass, freezer, 20)
    assert client.energy_stream_requests == 2


async def test_energy_stream_updates_the_joined_values(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)
    entities = len(er.async_get(hass).entities)
    soc_before = _state(hass, "battery_soc").state

    await _stream(hass)

    assert _shown(hass) == {key: (value, "mqtt") for key, value in FED.items()}
    # No new entities; the state of charge is not taken from the stream
    assert len(er.async_get(hass).entities) == entities
    assert _state(hass, "battery_soc").state == soc_before
    assert _state(hass, "battery_soc").attributes["source"] != "mqtt"


async def test_energy_stream_kept_while_the_cloud_is_behind(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)

    await _stream(hass)
    # The next HTTP request shows the older values of the cloud
    await _poll(hass, cloud_entry, api, "2026-11-19 20:00:10")

    assert _shown(hass) == {key: (value, "mqtt") for key, value in FED.items()}


async def test_energy_stream_hold_ends_after_30_seconds(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    api = await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)
    http = cloud_entry.runtime_data.cloud.data[f"{CLOUD_SN}_sysLoadPwr"]

    await _stream(hass)
    # No more reports (e.g. connection lost): the cloud's values count again
    freezer.tick(timedelta(seconds=31))
    await _poll(hass, cloud_entry, api, "2026-11-19 20:00:40")

    assert _shown(hass)["house_power"] == (float(round(http)), "cloud")


async def test_a_stream_after_mqtt_stopped_is_ignored(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)
    cloud = cloud_entry.runtime_data.cloud
    await cloud.async_stop_mqtt()

    await _stream(hass)

    assert cloud.live == {}


# Review fix: the cloud's own values the stream updates, and dual installations,
# as powerocean_single_phase (coordinator.py _stream_datapoints, _is_dual).

HEARTBEAT = f"{CLOUD_SN}_JTS1_EMS_HEARTBEAT_mpptHeartBeat"
# What the report changes: values under "data", PV strings, calculated flows
STREAM_DATAPOINTS = {
    f"{CLOUD_SN}_sysLoadPwr": 1760.0,
    f"{CLOUD_SN}_sysGridPwr": 10.0,
    f"{CLOUD_SN}_mpptPwr": 1110.0,
    f"{CLOUD_SN}_bpPwr": -640.0,
    f"{CLOUD_SN}_JTS1_ENERGY_STREAM_REPORT_pv1Pwr": 760.0,
    f"{CLOUD_SN}_JTS1_ENERGY_STREAM_REPORT_pv2Pwr": 340.0,
    f"{HEARTBEAT}_mpptPv_pwrTotal": 1110.0,
    f"{HEARTBEAT}_housePower": 1760.0,
    f"{HEARTBEAT}_gridPower": 10.0,
    f"{HEARTBEAT}_solarToHouse": 1110.0,
    f"{HEARTBEAT}_batteryToHouse": 640.0,
    f"{HEARTBEAT}_gridToHouse": 10.0,
    f"{HEARTBEAT}_solarToBattery": 0.0,
    f"{HEARTBEAT}_solarToGrid": 0.0,
}


def _cloud_values(entry: MockConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data.cloud.data
    return {unique_id: data.get(unique_id) for unique_id in STREAM_DATAPOINTS}


async def test_energy_stream_updates_the_cloud_values(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)
    cloud = cloud_entry.runtime_data.cloud
    datapoints = set(cloud.data)

    await _stream(hass)

    assert _cloud_values(cloud_entry) == STREAM_DATAPOINTS
    # Solar is a cloud-only value: its sensor shows the stream.
    assert float(_state(hass, "cloud_solar_power").state) == 1110
    # No datapoints the HTTP response does not have
    assert set(cloud.data) == datapoints
    assert f"{CLOUD_SN}_bpSoc" not in cloud.data


async def test_the_cloud_values_are_kept_while_the_cloud_is_behind(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)

    await _stream(hass)
    await _poll(hass, cloud_entry, api, "2026-11-19 20:00:10")

    assert _cloud_values(cloud_entry) == STREAM_DATAPOINTS


async def test_the_cloud_values_hold_ends_after_30_seconds(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    api = await _setup(hass, cloud_entry, set_up_hybrid, mqtt=True, live_power=True)
    http = cloud_entry.runtime_data.cloud.data[f"{CLOUD_SN}_mpptPwr"]

    await _stream(hass)
    freezer.tick(timedelta(seconds=31))
    await _poll(hass, cloud_entry, api, "2026-11-19 20:00:40")

    assert cloud_entry.runtime_data.cloud.data[f"{CLOUD_SN}_mpptPwr"] == http


async def test_energy_stream_ignored_in_dual_installations(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    response = _at("2026-11-19 20:00:00")
    quota = response["data"]["quota"]
    response["data"]["parallel"] = {CLOUD_SN: quota, "SN_SECOND": quota}
    await _setup(
        hass,
        cloud_entry,
        set_up_hybrid,
        mqtt=True,
        live_power=True,
        api=FakeApi(fetch=[response]),
    )
    cloud = cloud_entry.runtime_data.cloud
    before = dict(cloud.data)

    await _stream(hass)

    # The stream of a dual installation is not mapped yet
    assert cloud.data == before
    assert cloud.live == {}
