"""The model-86 entity catalogue: one entity per value, each with its sources."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.catalogue import (
    CATALOGUE_86,
    cloud_value,
)
from custom_components.ef_powerocean_hybrid.cloud.parse import parse
from custom_components.ef_powerocean_hybrid.models import InverterModel

FIXTURE = Path(__file__).parent / "cloud" / "fixtures" / "response_model86.json"
SINGLE = InverterModel.POWEROCEAN_SINGLE_PHASE


def _by_kind(kind: str) -> set[str]:
    return {entry.key for entry in CATALOGUE_86 if entry.kind == kind}


def test_every_key_is_unique() -> None:
    counts = Counter(entry.key for entry in CATALOGUE_86)
    assert [key for key, n in counts.items() if n > 1] == []


def test_the_joined_values_are_the_proven_pairs() -> None:
    assert _by_kind("joined") == {
        "house_power",
        "battery_power",
        "grid_power",
        "battery_soc",
        "voltage_l1",
        "frequency",
    }


def test_the_counters_with_a_fixed_source_are_the_battery_totals() -> None:
    assert _by_kind("counter") == {"bat_charged_total", "bat_discharged_total"}


def test_joined_values_and_counters_have_both_sources() -> None:
    for entry in CATALOGUE_86:
        if entry.kind in ("joined", "counter"):
            assert entry.modbus_key, entry.key
            assert entry.cloud, entry.key
        elif entry.kind == "modbus":
            assert entry.modbus_key, entry.key
            assert entry.cloud is None, entry.key
        else:
            assert entry.cloud, entry.key
            assert entry.modbus_key is None, entry.key


def test_joined_values_name_the_report_time_that_shows_freshness() -> None:
    times = {
        e.key: e.cloud.time_key for e in CATALOGUE_86 if e.kind == "joined" and e.cloud
    }
    assert times == {
        "house_power": "energyStreamUpdateTime",
        "battery_power": "energyStreamUpdateTime",
        "grid_power": "energyStreamUpdateTime",
        # Sent only when a value changes: its time says nothing of freshness.
        "battery_soc": None,
        "voltage_l1": "heartbeatUpdateTime",
        "frequency": "heartbeatUpdateTime",
    }


def test_the_counters_turn_the_cloud_watt_hours_into_kilowatt_hours() -> None:
    for entry in CATALOGUE_86:
        if entry.kind == "counter":
            assert entry.cloud is not None
            assert entry.cloud.factor == 0.001


def test_modbus_values_are_sensors_of_the_single_phase() -> None:
    known = {d.key for d in const.SENSOR_MAP if SINGLE not in d.unsupported_models}
    known |= {d.key for d in const.ENERGY_SENSOR_MAP}
    for entry in CATALOGUE_86:
        if entry.modbus_key is not None:
            assert entry.modbus_key in known, entry.key


def test_no_two_entities_share_a_source() -> None:
    modbus = Counter(e.modbus_key for e in CATALOGUE_86 if e.modbus_key)
    cloud = Counter((e.cloud.report, e.cloud.key) for e in CATALOGUE_86 if e.cloud)
    assert [k for k, n in modbus.items() if n > 1] == []
    assert [k for k, n in cloud.items() if n > 1] == []


def test_the_heartbeat_grid_and_battery_values_are_cloud_only() -> None:
    cloud_only = {e.cloud.key for e in CATALOGUE_86 if e.kind == "cloud" and e.cloud}
    assert {"pcsMeterPower", "emsBpPower"} <= cloud_only


def test_phases_b_and_c_are_left_out() -> None:
    for entry in CATALOGUE_86:
        if entry.cloud is not None:
            assert "pcsBPhase" not in entry.cloud.key, entry.key
            assert "pcsCPhase" not in entry.cloud.key, entry.key
        assert entry.modbus_key not in {"voltage_l2", "voltage_l3", "current_l2"}


def test_every_cloud_source_is_in_a_real_model86_response() -> None:
    values = parse(json.loads(FIXTURE.read_text()), "SN_MAIN", "86").values()
    for entry in CATALOGUE_86:
        if entry.cloud is None:
            continue
        serials = ("SN_BP01", "SN_BP02") if entry.per_pack else ("SN_MAIN",)
        for serial in serials:
            assert cloud_value(values, serial, entry.cloud) is not None, (
                entry.key,
                serial,
            )


def test_a_missing_cloud_value_is_none() -> None:
    entry = next(e for e in CATALOGUE_86 if e.key == "house_power")
    assert entry.cloud is not None
    assert cloud_value({}, "SN_MAIN", entry.cloud) is None


def test_the_cloud_factor_scales_the_value() -> None:
    entry = next(e for e in CATALOGUE_86 if e.key == "bat_charged_total")
    assert entry.cloud is not None
    values = {"SN_MAIN_JTS1_EMS_CHANGE_REPORT_bpTotalChgEnergy": 3_268_688}
    assert cloud_value(values, "SN_MAIN", entry.cloud) == 3268.688


def test_cloud_only_values_keep_the_cloud_unit_and_sign() -> None:
    """Dashboards on the cloud entities carry on unchanged at the cut-over."""
    for entry in CATALOGUE_86:
        if entry.kind == "cloud":
            assert entry.cloud is not None
            assert entry.cloud.factor == 1, entry.key


def test_the_pack_counters_stay_in_watt_hours() -> None:
    for key in ("pack_charged_total", "pack_discharged_total"):
        entry = next(e for e in CATALOGUE_86 if e.key == key)
        assert entry.description.unit == "Wh"


def test_soc_comes_from_the_change_report_as_in_the_cloud_integration() -> None:
    source = next(e.cloud for e in CATALOGUE_86 if e.key == "battery_soc")
    assert source is not None
    assert (source.report, source.key) == ("EMS_CHANGE_REPORT", "bpSoc")
