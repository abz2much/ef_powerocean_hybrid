"""A real PowerOcean Single Phase (cloud model 86) response parses as expected."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from custom_components.ef_powerocean_hybrid.cloud.parse import parse

FIXTURE = Path(__file__).parent / "fixtures" / "response_model86.json"
SN = "SN_MAIN"


def _values() -> dict[str, Any]:
    response = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return parse(response, SN, "86").values()


def _suffix(values: dict[str, Any], suffix: str) -> Any:
    matches = [value for key, value in values.items() if key.endswith(suffix)]
    assert len(matches) == 1, suffix
    return matches[0]


def test_the_inverter_values_are_read() -> None:
    values = _values()

    assert _suffix(values, "_EMS_HEARTBEAT_pcsAPhase_vol") > 200
    assert 0 <= _suffix(values, "_EMS_CHANGE_REPORT_bpSoc") <= 100
    assert f"{SN}_sysLoadPwr" in values
    assert f"{SN}_sysGridPwr" in values
    assert f"{SN}_bpPwr" in values


def test_both_battery_packs_are_read() -> None:
    values = _values()

    for pack in ("SN_BP01", "SN_BP02"):
        assert any(
            key.startswith(f"{pack}_") and key.endswith("_bpSoc") for key in values
        )


def test_phases_b_and_c_read_zero_on_a_single_phase() -> None:
    values = _values()

    for phase in "BC":
        assert _suffix(values, f"_EMS_HEARTBEAT_pcs{phase}Phase_vol") == 0


def test_the_energy_stream_soc_is_read() -> None:
    """The system SOC's backup when Modbus is down: refreshed every 30 s."""
    assert 0 <= _suffix(_values(), "_ENERGY_STREAM_REPORT_bpSoc") <= 100
