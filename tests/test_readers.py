"""The readings each source gives a joined value, and when they count as fresh."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.catalogue import CATALOGUE_86
from custom_components.ef_powerocean_hybrid.cloud.coordinator import CloudCoordinator
from custom_components.ef_powerocean_hybrid.sources import (
    Reading,
    http_reading,
    modbus_reading,
    mqtt_reading,
    pick,
)
from tests.cloud.fakes import CLOUD_SN, FakeApi, model86_response

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
HTTP_LIMIT = timedelta(seconds=60)


def _source(key: str) -> Any:
    return next(e.cloud for e in CATALOGUE_86 if e.key == key)


# Modbus: online, present, trusted and current.


def _modbus(**changes: Any) -> Any:
    fields = {
        "last_update_success": True,
        "connected": True,
        "data": {"house_power": 2083.0},
        "control": SimpleNamespace(
            reading_checks=SimpleNamespace(distrusted=frozenset())
        ),
        "last_good_poll": NOW - timedelta(seconds=4),
        "scan_interval": 5,
    } | changes
    return cast(Any, SimpleNamespace(**fields))


def test_a_good_modbus_poll_gives_a_reading() -> None:
    assert modbus_reading(_modbus(), "house_power", NOW) == Reading(
        2083.0, NOW - timedelta(seconds=4)
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"last_update_success": False},
        {"connected": False},
        {"data": {}},
        {"data": None},
        {
            "control": SimpleNamespace(
                reading_checks=SimpleNamespace(distrusted=frozenset({"house_power"}))
            )
        },
        {"last_good_poll": NOW - timedelta(seconds=16)},
        {"last_good_poll": None},
    ),
    ids=(
        "failed",
        "disconnected",
        "missing",
        "no data",
        "distrusted",
        "older than 3 polls",
        "never",
    ),
)
def test_modbus_gives_no_reading(changes: dict[str, Any]) -> None:
    assert modbus_reading(_modbus(**changes), "house_power", NOW) is None


def test_modbus_three_polls_old_still_counts() -> None:
    assert modbus_reading(
        _modbus(last_good_poll=NOW - timedelta(seconds=15)), "house_power", NOW
    )


def test_the_coordinator_reports_its_last_good_poll() -> None:
    from custom_components.ef_powerocean_hybrid.coordinator import EcoflowCoordinator

    coordinator = EcoflowCoordinator.__new__(EcoflowCoordinator)
    coordinator._last_checked_time = NOW

    assert coordinator.last_good_poll == NOW


# HTTP: fresh on Home Assistant's clock, from when a report time last changed.


def _with_report_times(stream: str, heartbeat: str) -> dict[str, Any]:
    response = copy.deepcopy(model86_response())
    quota = response["data"]["quota"]
    quota["JTS1_ENERGY_STREAM_REPORT"]["updateTime"] = stream
    quota["JTS1_EMS_HEARTBEAT"]["updateTime"] = heartbeat
    return response


async def _cloud(hass: HomeAssistant, api: FakeApi) -> CloudCoordinator:
    entry = MockConfigEntry(domain=const.DOMAIN, data={})
    entry.add_to_hass(hass)
    return CloudCoordinator(hass, entry, api)


async def test_a_report_seen_once_is_not_fresh(hass: HomeAssistant) -> None:
    """After a restart a dead cloud still returns its last report time."""
    cloud = await _cloud(hass, FakeApi(fetch=[model86_response()]))
    await cloud.async_refresh()

    assert http_reading(cloud, CLOUD_SN, _source("house_power")) is None


async def test_a_report_counts_from_when_its_time_changed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    api = FakeApi(
        fetch=[_with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:00")]
    )
    cloud = await _cloud(hass, api)
    await cloud.async_refresh()
    freezer.tick(timedelta(seconds=30))
    api.respond(_with_report_times("2026-11-19 20:00:30", "2026-11-19 20:00:00"))
    await cloud.async_refresh()

    reading = http_reading(cloud, CLOUD_SN, _source("house_power"))

    assert reading is not None
    assert reading.at == dt_util.utcnow()
    assert reading.value == cloud.data[f"{CLOUD_SN}_sysLoadPwr"]
    # The heartbeat time did not change, so its values are not fresh yet.
    assert http_reading(cloud, CLOUD_SN, _source("voltage_l1")) is None


async def test_a_fast_cloud_clock_cannot_keep_a_dead_value_fresh(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The cloud's clock runs 5 minutes fast, then the cloud stops updating."""
    freezer.move_to("2026-11-19 12:00:00+00:00")
    # Server time is UTC+8; 5 minutes fast.
    api = FakeApi(
        fetch=[_with_report_times("2026-11-19 20:05:00", "2026-11-19 20:05:00")]
    )
    cloud = await _cloud(hass, api)
    await cloud.async_refresh()
    freezer.tick(timedelta(seconds=30))
    api.respond(_with_report_times("2026-11-19 20:05:30", "2026-11-19 20:05:00"))
    await cloud.async_refresh()
    assert http_reading(cloud, CLOUD_SN, _source("house_power")) is not None

    # The cloud stops: the same report keeps coming back.
    freezer.tick(timedelta(seconds=61))
    await cloud.async_refresh()
    reading = http_reading(cloud, CLOUD_SN, _source("house_power"))

    assert (
        pick(
            None,
            None,
            reading,
            now=dt_util.utcnow(),
            mqtt_limit=timedelta(seconds=30),
            http_limit=HTTP_LIMIT,
        )
        is None
    )


async def test_a_report_missing_then_back_is_not_fresh(hass: HomeAssistant) -> None:
    """A report absent from one response and back in the next has not changed."""
    without_stream = _with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:00")
    del without_stream["data"]["quota"]["JTS1_ENERGY_STREAM_REPORT"]
    api = FakeApi(
        fetch=[_with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:00")]
    )
    cloud = await _cloud(hass, api)
    await cloud.async_refresh()
    api.respond(without_stream)
    await cloud.async_refresh()
    api.respond(_with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:00"))
    await cloud.async_refresh()

    assert http_reading(cloud, CLOUD_SN, _source("house_power")) is None


async def test_a_missing_value_gives_no_reading(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    api = FakeApi(
        fetch=[_with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:00")]
    )
    cloud = await _cloud(hass, api)
    await cloud.async_refresh()
    freezer.tick(timedelta(seconds=30))
    response = _with_report_times("2026-11-19 20:00:30", "2026-11-19 20:00:00")
    del response["data"]["sysLoadPwr"]
    api.respond(response)
    await cloud.async_refresh()

    assert http_reading(cloud, CLOUD_SN, _source("house_power")) is None


async def test_text_where_a_number_belongs_gives_no_reading(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    api = FakeApi(
        fetch=[_with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:00")]
    )
    cloud = await _cloud(hass, api)
    await cloud.async_refresh()
    freezer.tick(timedelta(seconds=30))
    response = _with_report_times("2026-11-19 20:00:30", "2026-11-19 20:00:00")
    response["data"]["sysLoadPwr"] = "FALSE"
    api.respond(response)
    await cloud.async_refresh()

    assert http_reading(cloud, CLOUD_SN, _source("house_power")) is None


# MQTT: filled in 3c.


async def test_mqtt_gives_no_reading_while_it_has_none(hass: HomeAssistant) -> None:
    cloud = await _cloud(hass, FakeApi(fetch=[model86_response()]))

    assert mqtt_reading(cloud, "house_power") is None


async def test_mqtt_gives_its_live_reading(hass: HomeAssistant) -> None:
    cloud = await _cloud(hass, FakeApi(fetch=[model86_response()]))
    cloud.live["house_power"] = Reading(2060.0, NOW)

    assert mqtt_reading(cloud, "house_power") == Reading(2060.0, NOW)


async def test_soc_counts_from_when_the_system_last_checked_in(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The change report is sent only on a change, so the heartbeat times it.

    A cloud that keeps answering also keeps the last SOC of a quiet system.
    """
    api = FakeApi(
        fetch=[_with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:00")]
    )
    cloud = await _cloud(hass, api)
    await cloud.async_refresh()
    # A heartbeat time seen once proves nothing.
    assert http_reading(cloud, CLOUD_SN, _source("battery_soc")) is None

    api.respond(_with_report_times("2026-11-19 20:00:00", "2026-11-19 20:00:30"))
    await cloud.async_refresh()
    checked_in = dt_util.utcnow()
    reading = http_reading(cloud, CLOUD_SN, _source("battery_soc"))
    assert reading is not None
    assert reading.value == cloud.data[f"{CLOUD_SN}_JTS1_EMS_CHANGE_REPORT_bpSoc"]
    assert reading.at == checked_in

    # The cloud still answers, but the heartbeat stands still.
    freezer.tick(timedelta(seconds=121))
    await cloud.async_refresh()
    later = http_reading(cloud, CLOUD_SN, _source("battery_soc"))
    assert cloud.last_update_success
    assert later is not None
    assert later.at == checked_in
