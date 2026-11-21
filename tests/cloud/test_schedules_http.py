"""The schedule list in the cloud HTTP response (`*_EMS_ALL_TIMER_TASK_REPORT`)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from custom_components.ef_powerocean_hybrid.cloud.schedules import schedules_from_http

FIXTURES = Path(__file__).parent / "fixtures"
REPORT = "JTS1_EMS_ALL_TIMER_TASK_REPORT"


def load(name: str) -> dict[str, Any]:
    """Return a fixture, parsed."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_the_owners_six_slots_are_read() -> None:
    """The owner's live response gives its six schedules."""
    schedules = schedules_from_http(load("response_model86_schedules.json"))
    assert schedules is not None
    assert sorted(
        (s.schedule_id, s.schedule_type, s.power, s.start, s.end, s.repeat, s.enabled)
        for s in schedules
    ) == [
        (13, 1, 3000, 120, 360, 1, True),
        (14, 1, 1500, 360, 480, 1, True),
        (16, 2, 5000, 480, 1020, 1, True),
        (17, 2, 5000, 1140, 1380, 1, True),
        (18, 2, 5000, 1020, 1140, 1, True),
        (19, 2, 2000, 1380, 120, 1, True),
    ]


def test_in_use_follows_is_effect() -> None:
    """Only the schedule the system runs now is in use."""
    schedules = schedules_from_http(load("response_model86_schedules.json"))
    assert schedules is not None
    assert [s.schedule_id for s in schedules if s.in_use] == [14]


def test_an_empty_list_is_no_schedules() -> None:
    """A response with an empty list gives no schedules, not unknown."""
    assert schedules_from_http(load("response_model86.json")) == []


def test_no_timer_report_is_unknown() -> None:
    """Without the report the list is unknown (None)."""
    response = copy.deepcopy(load("response_model86_schedules.json"))
    del response["data"]["quota"][REPORT]
    assert schedules_from_http(response) is None


def test_the_prefix_of_the_key_may_differ() -> None:
    """The model decides the prefix (JT303_ ...); any such key is read."""
    response = copy.deepcopy(load("response_model86_schedules.json"))
    quota = response["data"]["quota"]
    quota["JT303_EMS_ALL_TIMER_TASK_REPORT"] = quota.pop(REPORT)
    schedules = schedules_from_http(response)
    assert schedules is not None
    assert len(schedules) == 6


def test_enabled_may_come_as_text() -> None:
    """isEnable is a bool or the text TRUE/FALSE."""
    response = copy.deepcopy(load("response_model86_schedules.json"))
    entries = response["data"]["quota"][REPORT]["timeTaskCfg"]
    entries[0]["isEnable"] = "FALSE"
    entries[1]["isEnable"] = "TRUE"
    entries[2]["isEnable"] = False
    schedules = schedules_from_http(response)
    assert schedules is not None
    enabled = {s.schedule_id: s.enabled for s in schedules}
    assert enabled[entries[0]["taskIndex"]] is False
    assert enabled[entries[1]["taskIndex"]] is True
    assert enabled[entries[2]["taskIndex"]] is False


def test_an_entry_without_a_time_is_skipped() -> None:
    """An entry whose timeTable is empty is not a schedule."""
    response = copy.deepcopy(load("response_model86_schedules.json"))
    entries = response["data"]["quota"][REPORT]["timeTaskCfg"]
    skipped = entries[0]["taskIndex"]
    entries[0]["timeTable"] = []
    schedules = schedules_from_http(response)
    assert schedules is not None
    assert skipped not in {s.schedule_id for s in schedules}
    assert len(schedules) == 5


def test_a_response_without_data_is_unknown() -> None:
    """A malformed response gives None."""
    assert schedules_from_http({}) is None
    assert schedules_from_http({"data": {"quota": None}}) is None
