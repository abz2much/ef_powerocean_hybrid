"""B1-B4, B7: one entity per joined value, Modbus first, the cloud as backup."""

from __future__ import annotations

import copy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.catalogue import CATALOGUE_86
from custom_components.ef_powerocean_hybrid.coordinator import EcoflowCoordinator
from custom_components.ef_powerocean_hybrid.joined_sensor import JoinedSensor
from custom_components.ef_powerocean_hybrid.sources import Reading
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

JOINED = (
    "house_power",
    "battery_power",
    "grid_power",
    "battery_soc",
    "voltage_l1",
    "frequency",
)


def _stream_at(time: str) -> dict[str, Any]:
    """The model-86 response with its energy stream and heartbeat reported at *time*."""
    response = copy.deepcopy(model86_response())
    quota = response["data"]["quota"]
    quota["JTS1_ENERGY_STREAM_REPORT"]["updateTime"] = time
    quota["JTS1_EMS_HEARTBEAT"]["updateTime"] = time
    return response


def _entity_id(hass: HomeAssistant, key: str, serial: str = SERIAL) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", const.DOMAIN, f"{serial}_{key}"
    )
    assert entity_id is not None, key
    return entity_id


def _state(hass: HomeAssistant, key: str, serial: str = SERIAL) -> Any:
    state = hass.states.get(_entity_id(hass, key, serial))
    assert state is not None, key
    return state


async def _cloud_fresh(
    hass: HomeAssistant, entry: MockConfigEntry, api: FakeApi
) -> None:
    """Have the cloud's report times change once, so its values count as fresh."""
    api.respond(_stream_at("2026-11-19 20:00:30"))
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()


async def _fail_modbus(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch.object(
        EcoflowCoordinator, "async_get_raw_data", side_effect=UpdateFailed("down")
    ):
        await entry.runtime_data.modbus.async_refresh()
    await hass.async_block_till_done()


async def test_each_joined_value_is_one_entity_fed_by_both(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    component = hass.data["entity_components"]["sensor"]
    for key in JOINED:
        assert isinstance(component.get_entity(_entity_id(hass, key)), JoinedSensor), (
            key
        )


async def test_modbus_comes_first(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(
        hybrid_entry, FakeApi(fetch=[_stream_at("2026-11-19 20:00:00")])
    )

    state = _state(hass, "house_power")

    assert state.attributes["source"] == "modbus"
    assert float(state.state) == hybrid_entry.runtime_data.modbus.data["house_power"]


async def test_the_cloud_takes_over_when_modbus_fails(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[_stream_at("2026-11-19 20:00:00")])
    await set_up_hybrid(hybrid_entry, api)
    await _cloud_fresh(hass, hybrid_entry, api)

    await _fail_modbus(hass, hybrid_entry)
    state = _state(hass, "house_power")

    assert state.attributes["source"] == "cloud"
    assert (
        float(state.state)
        == hybrid_entry.runtime_data.cloud.data[f"{CLOUD_SN}_sysLoadPwr"]
    )


async def test_a_stale_cloud_and_no_modbus_is_unavailable(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    api = FakeApi(fetch=[_stream_at("2026-11-19 20:00:00")])
    await set_up_hybrid(hybrid_entry, api)
    await _cloud_fresh(hass, hybrid_entry, api)
    await _fail_modbus(hass, hybrid_entry)

    # No update arrives from either side; the entity checks again on its own.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    assert _state(hass, "house_power").state == "unavailable"


async def test_a_distrusted_soc_falls_back_to_the_cloud(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[_stream_at("2026-11-19 20:00:00")])
    await set_up_hybrid(hybrid_entry, api)
    hybrid_entry.runtime_data.modbus.control.reading_checks.distrusted = frozenset(
        {"battery_soc"}
    )

    await _cloud_fresh(hass, hybrid_entry, api)

    assert _state(hass, "battery_soc").attributes["source"] == "cloud"


async def test_a_modbus_only_single_phase_keeps_its_modbus_sensors(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry
) -> None:
    """A4: without a cloud connection nothing changes."""
    data = {
        k: v
        for k, v in hybrid_entry.data.items()
        if k in (const.CONF_HOST, const.CONF_PORT)
    }
    hass.config_entries.async_update_entry(hybrid_entry, data=data)
    assert await hass.config_entries.async_setup(hybrid_entry.entry_id)
    await hass.async_block_till_done()

    component = hass.data["entity_components"]["sensor"]
    assert not isinstance(
        component.get_entity(_entity_id(hass, "house_power")), JoinedSensor
    )


async def test_a_cloud_only_entry_has_the_joined_values_from_the_cloud(
    hass: HomeAssistant, set_up_hybrid: SetUp
) -> None:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=CLOUD_SN,
        minor_version=2,
        data={
            "email": "owner@example.com",
            "password": "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        },
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    api = FakeApi(fetch=[_stream_at("2026-11-19 20:00:00")])
    await set_up_hybrid(entry, api)
    await _cloud_fresh(hass, entry, api)

    for key in JOINED:
        assert _state(hass, key, CLOUD_SN).attributes["source"] == "cloud", key

    await hass.config_entries.async_unload(entry.entry_id)


# One joined sensor, driven directly.


def _sensor(key: str, modbus: Any, cloud: Any) -> JoinedSensor:
    definition = next(e for e in CATALOGUE_86 if e.key == key)
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        data={const.CONF_CLOUD_SERIAL: CLOUD_SN},
    )
    return JoinedSensor(entry, definition, cast(Any, modbus), cast(Any, cloud))


def _fake_modbus(key: str, value: float) -> SimpleNamespace:
    return SimpleNamespace(
        last_update_success=True,
        connected=True,
        data={key: value},
        control=SimpleNamespace(reading_checks=SimpleNamespace(distrusted=frozenset())),
        last_good_poll=dt_util.utcnow(),
        scan_interval=5,
    )


def _fake_cloud(key: str, value: float) -> SimpleNamespace:
    return SimpleNamespace(
        data={}, report_seen={}, live={key: Reading(value, dt_util.utcnow())}
    )


@pytest.mark.parametrize(
    ("key", "modbus", "cloud"),
    (
        ("house_power", 2083.0, 2060.0),
        ("battery_power", -1927.0, -1920.0),
        ("grid_power", -2.0, 0.0),
        ("battery_soc", 79.0, 79.0),
    ),
)
def test_recorded_values_keep_their_unit_and_sign(
    key: str, modbus: float, cloud: float
) -> None:
    """B4: the 19 Nov 2026 readings of both sources, shown the same way."""
    from_modbus = _sensor(key, _fake_modbus(key, modbus), _fake_cloud(key, cloud))
    from_cloud = _sensor(key, None, _fake_cloud(key, cloud))
    from_modbus.update_from_sources()
    from_cloud.update_from_sources()

    assert from_modbus.native_value == modbus
    assert from_cloud.native_value == cloud
    assert (
        from_modbus.native_unit_of_measurement == from_cloud.native_unit_of_measurement
    )
    assert (modbus < 0) == (cloud < 0) or 0 in (modbus, cloud)


def test_the_source_is_not_recorded() -> None:
    sensor = _sensor("house_power", None, _fake_cloud("house_power", 1.0))
    sensor.update_from_sources()

    assert sensor.extra_state_attributes == {"source": "mqtt"}
    assert "source" in sensor._unrecorded_attributes


def test_the_unique_id_is_the_modbus_sensors() -> None:
    """The entity ID stays the same when the cloud is added or removed."""
    assert _sensor("house_power", None, None).unique_id == f"{SERIAL}_house_power"


# Review fixes: the state looks as it did as a Modbus sensor.


@pytest.mark.parametrize(
    ("key", "value", "shown"),
    (
        ("house_power", 513.4, 513),
        ("battery_soc", 70.0, 70),
        ("voltage_l1", 243.50615, 243.5),
        ("frequency", 50.0712, 50.07),
    ),
)
def test_values_are_rounded_like_the_modbus_sensors(
    key: str, value: float, shown: float
) -> None:
    """Dashboards, templates and Homey read these entity IDs as they are today."""
    from custom_components.ef_powerocean_hybrid.display import shown_number

    definition = next(e for e in CATALOGUE_86 if e.key == key)
    sensor = _sensor(key, _fake_modbus(key, value), None)
    sensor.update_from_sources()

    assert sensor.native_value == shown
    assert type(sensor.native_value) is type(shown)
    assert sensor.native_value == shown_number(value, definition.description.unit)


def test_a_dropped_modbus_connection_hands_over_at_once() -> None:
    modbus = _fake_modbus("house_power", 2083.0)
    modbus.connected = False
    sensor = _sensor("house_power", modbus, _fake_cloud("house_power", 2060.0))
    sensor.update_from_sources()

    assert sensor.extra_state_attributes == {"source": "mqtt"}


async def test_a_recheck_that_changes_nothing_writes_nothing(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(
        hybrid_entry, FakeApi(fetch=[_stream_at("2026-11-19 20:00:00")])
    )
    entity = hass.data["entity_components"]["sensor"].get_entity(
        _entity_id(hass, "house_power")
    )

    with patch.object(entity, "async_write_ha_state") as write:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done()

    write.assert_not_called()
