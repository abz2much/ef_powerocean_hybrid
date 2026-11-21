"""The schedule list from MQTT, from the HTTP response or from a saved copy."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ef_powerocean_hybrid import async_remove_entry, const
from custom_components.ef_powerocean_hybrid.cloud.api import EcoflowConnectionError
from custom_components.ef_powerocean_hybrid.cloud.coordinator import CloudCoordinator
from custom_components.ef_powerocean_hybrid.cloud.schedules import (
    ScheduleList,
    schedules_from_http,
)
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import FakeApi, model86_response
from tests.cloud.schedule_fakes import ScheduleMqtt, schedule_system, system_schedules
from tests.cloud.test_schedules_http import load

SAVE_DELAY = const.STATE_SAVE_DELAY_S + 1


def http_response() -> dict[str, Any]:
    return load("response_model86_schedules.json")


def http_schedules() -> tuple[Any, ...]:
    found = schedules_from_http(http_response())
    assert found is not None
    return tuple(sorted(found, key=lambda s: s.schedule_id))


async def start(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    set_up: SetUp,
    api: FakeApi,
    *,
    mqtt: bool,
) -> CloudCoordinator:
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, const.CONF_MQTT: mqtt}
    )
    await set_up(entry, api)
    cloud = entry.runtime_data.cloud
    assert cloud is not None
    return cloud


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(seconds=SAVE_DELAY))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def key_of(entry: MockConfigEntry) -> str:
    return f"{const.DOMAIN}.{entry.entry_id}.schedules"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_mqtt_list_comes_first(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    with schedule_system():
        cloud = await start(
            hass,
            cloud_entry,
            set_up_hybrid,
            FakeApi(fetch=[http_response()]),
            mqtt=True,
        )
        found = cloud.schedule_list()
    assert found == ScheduleList(
        tuple(sorted(system_schedules(), key=lambda s: s.schedule_id)),
        "mqtt",
        True,
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_http_list_stands_in_without_mqtt(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    cloud = await start(
        hass,
        cloud_entry,
        set_up_hybrid,
        FakeApi(fetch=[http_response()]),
        mqtt=False,
    )
    found = cloud.schedule_list()
    assert found == ScheduleList(http_schedules(), "http", False)
    assert len(found.schedules) == 6


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("mqtt", "source", "trusted"), ((False, "http", False), (True, "mqtt", True))
)
async def test_the_saved_list_stands_in_without_the_cloud(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    mqtt: bool,
    source: str,
    trusted: bool,
) -> None:
    with schedule_system():
        first = await start(
            hass,
            cloud_entry,
            set_up_hybrid,
            FakeApi(fetch=[http_response()]),
            mqtt=mqtt,
        )
        before = first.schedule_list()
        assert before is not None
        assert before.source == source
        await tick(hass, freezer)
        await hass.config_entries.async_unload(cloud_entry.entry_id)
        await hass.async_block_till_done()

        down = FakeApi(fetch=[EcoflowConnectionError("down")])
        second = await start(hass, cloud_entry, set_up_hybrid, down, mqtt=False)
    found = second.schedule_list()
    # The saved copy keeps no unknown_2
    expected = tuple(replace(s, unknown_2=None) for s in before.schedules)
    assert found == ScheduleList(expected, "saved", trusted)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_no_list_from_anywhere_is_none(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    cloud = await start(
        hass,
        cloud_entry,
        set_up_hybrid,
        FakeApi(fetch=[model86_response() | {"data": {}}]),
        mqtt=False,
    )
    assert cloud.schedule_list() is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_changed_list_is_saved(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
) -> None:
    api = FakeApi(fetch=[http_response()])
    cloud = await start(hass, cloud_entry, set_up_hybrid, api, mqtt=False)
    assert key_of(cloud_entry) not in hass_storage
    await tick(hass, freezer)
    saved = hass_storage[key_of(cloud_entry)]
    assert saved["version"] == 1
    assert saved["data"]["source"] == "http"
    assert [s["schedule_id"] for s in saved["data"]["schedules"]] == [
        13, 14, 16, 17, 18, 19,
    ]  # fmt: skip
    assert set(saved["data"]["schedules"][0]) == {
        "schedule_id",
        "schedule_type",
        "enabled",
        "power",
        "start",
        "end",
        "repeat",
        "days",
        "in_use",
    }

    # An unchanged list is not saved again
    del hass_storage[key_of(cloud_entry)]
    await cloud.async_refresh()
    await tick(hass, freezer)
    assert key_of(cloud_entry) not in hass_storage

    # A changed list is
    changed = http_response()
    report = changed["data"]["quota"]["JTS1_EMS_ALL_TIMER_TASK_REPORT"]
    report["timeTaskCfg"] = report["timeTaskCfg"][:2]
    api.respond(changed)
    await cloud.async_refresh()
    await tick(hass, freezer)
    assert len(hass_storage[key_of(cloud_entry)]["data"]["schedules"]) == 2


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_changed_mqtt_list_is_saved(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
) -> None:
    with schedule_system():
        await start(
            hass,
            cloud_entry,
            set_up_hybrid,
            FakeApi(fetch=[http_response()]),
            mqtt=True,
        )
        await tick(hass, freezer)
    saved = hass_storage[key_of(cloud_entry)]["data"]
    assert saved["source"] == "mqtt"
    assert [s["schedule_id"] for s in saved["schedules"]] == [10, 11, 12]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_http_list_does_not_replace_the_saved_mqtt_list(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
) -> None:
    """With MQTT on, a restart before MQTT connects keeps the saved MQTT list."""
    with schedule_system():
        await start(
            hass,
            cloud_entry,
            set_up_hybrid,
            FakeApi(fetch=[http_response()]),
            mqtt=True,
        )
        await tick(hass, freezer)
        await hass.config_entries.async_unload(cloud_entry.entry_id)
        await hass.async_block_till_done()
        assert hass_storage[key_of(cloud_entry)]["data"]["source"] == "mqtt"

        # The cloud answers, MQTT gives no list yet
        ScheduleMqtt.answer_lists = False
        cloud = await start(
            hass,
            cloud_entry,
            set_up_hybrid,
            FakeApi(fetch=[http_response()]),
            mqtt=True,
        )
        found = cloud.schedule_list()
        assert found is not None
        assert found.source == "http"
        await tick(hass, freezer)
    saved = hass_storage[key_of(cloud_entry)]["data"]
    assert saved["source"] == "mqtt"
    assert [s["schedule_id"] for s in saved["schedules"]] == [10, 11, 12]


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "data",
    (
        None,
        {"source": "mqtt"},
        {"source": "other", "schedules": []},
        {"source": "http", "schedules": [{"schedule_id": 1}]},
        {"source": "http", "schedules": "x"},
    ),
)
async def test_a_damaged_saved_list_is_ignored(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    data: Any,
) -> None:
    hass_storage[key_of(cloud_entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": key_of(cloud_entry),
        "data": data,
    }
    cloud = await start(
        hass,
        cloud_entry,
        set_up_hybrid,
        FakeApi(fetch=[EcoflowConnectionError("down")]),
        mqtt=False,
    )
    assert cloud.schedule_list() is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removing_the_entry_deletes_the_saved_list(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
) -> None:
    await start(
        hass,
        cloud_entry,
        set_up_hybrid,
        FakeApi(fetch=[http_response()]),
        mqtt=False,
    )
    await tick(hass, freezer)
    assert key_of(cloud_entry) in hass_storage
    await hass.config_entries.async_unload(cloud_entry.entry_id)
    await hass.async_block_till_done()

    await async_remove_entry(hass, cloud_entry)

    assert key_of(cloud_entry) not in hass_storage
