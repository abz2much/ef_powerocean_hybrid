"""
schedules.py: names, attributes and service input of the scheduled tasks.

The title says what a schedule does ("Charge 02:00-06:00 3000 W"); the
attributes are what the switches and the sensor show; the service input of
the actions (names of days, times, ...) becomes the arguments of the
coordinator.

This module does not depend on Home Assistant.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, NamedTuple

from .mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_REPEAT_DAYS,
    SCHEDULE_USE_BATTERY,
    Schedule,
    unpack_schedule_time,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import time

# Type of a schedule: key in the actions and attributes, and title
TYPE_KEYS = {SCHEDULE_CHARGE: "charge", SCHEDULE_USE_BATTERY: "use_battery"}
TYPE_BY_KEY = {key: number for number, key in TYPE_KEYS.items()}
TYPE_TITLES = {SCHEDULE_CHARGE: "Charge", SCHEDULE_USE_BATTERY: "Use battery"}
# Days of the week as the system has them: Monday 1 to Sunday 64
DAY_BITS = {"mon": 1, "tue": 2, "wed": 4, "thu": 8, "fri": 16, "sat": 32, "sun": 64}


# Keys of the error texts for wrong input of an action
KEY_INVALID_POWER = "schedule_invalid_power"
KEY_INVALID_TIME = "schedule_invalid_time"
KEY_DAYS_AND_DAILY = "schedule_days_and_daily"


# Minutes in a day: a window may run over midnight
DAY_MINUTES = 24 * 60


class ScheduleInputError(ValueError):
    """The input of an action is wrong; `key` is the key of the error text."""

    def __init__(self, key: str) -> None:
        """Keep the key of the error text."""
        super().__init__(key)
        self.key = key


def within_the_day(schedule: Schedule) -> bool:
    """Return whether both times of a schedule are times of the day (to 23:59)."""
    return 0 <= schedule.start < DAY_MINUTES and 0 <= schedule.end < DAY_MINUTES


def format_minutes(minutes: int) -> str:
    """Return minutes since midnight as HH:MM."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def schedule_title(schedule: Schedule) -> str:
    """Return what a schedule does, e.g. "Charge 02:00-06:00 3000 W"."""
    kind = TYPE_TITLES.get(schedule.schedule_type, "Schedule")
    return (
        f"{kind} {format_minutes(schedule.start)}-{format_minutes(schedule.end)} "
        f"{schedule.power} W"
    )


def days_mask(names: Iterable[str]) -> int:
    """Return the bit mask of days given by name (mon ... sun)."""
    mask = 0
    for name in names:
        mask |= DAY_BITS[name]
    return mask


def day_names(mask: int) -> list[str]:
    """Return the names of the days in a bit mask, Monday first."""
    return [name for name, bit in DAY_BITS.items() if mask & bit]


def schedule_days(schedule: Schedule) -> str | list[str]:
    """
    Return "daily" or the chosen days.

    The app shows the older schedules with repeat 65 as Daily.
    """
    if schedule.repeat != SCHEDULE_REPEAT_DAYS:
        return "daily"
    return day_names(schedule.days)


def schedule_attributes(schedule: Schedule) -> dict[str, Any]:
    """Return the attributes of the switch of a schedule."""
    return {
        "id": schedule.schedule_id,
        "type": TYPE_KEYS.get(schedule.schedule_type, f"type_{schedule.schedule_type}"),
        "start": format_minutes(schedule.start),
        "end": format_minutes(schedule.end),
        "power": schedule.power,
        "days": schedule_days(schedule),
        "in_use": schedule.in_use,
    }


SOURCE_MQTT = "mqtt"
SOURCE_HTTP = "http"
SOURCE_SAVED = "saved"
SAVED_FIELDS = (
    "schedule_id",
    "schedule_type",
    "enabled",
    "power",
    "start",
    "end",
    "repeat",
    "days",
    "in_use",
)


class ScheduleList(NamedTuple):
    """
    The schedules and where they come from.

    `source` is "mqtt", "http" or "saved". `days_trusted` says whether the
    chosen days are right: the MQTT list has them, the HTTP list has none, so
    its schedules (and a saved copy of it) cannot tell which days they run.
    """

    schedules: tuple[Schedule, ...]
    source: str
    days_trusted: bool


def saved_list(schedules: Iterable[Schedule], source: str) -> dict[str, Any]:
    """Return a list of schedules as it is saved; `source` is its origin."""
    return {
        "source": source,
        "schedules": [
            {name: getattr(schedule, name) for name in SAVED_FIELDS}
            for schedule in schedules
        ],
    }


def schedules_from_saved(data: Any) -> ScheduleList | None:
    """Return the list saved by `saved_list`, None if it is missing or damaged."""
    if not isinstance(data, dict) or data.get("source") not in (
        SOURCE_MQTT,
        SOURCE_HTTP,
    ):
        return None
    entries = data.get("schedules")
    if not isinstance(entries, list):
        return None
    try:
        schedules = tuple(
            Schedule(**{name: entry[name] for name in SAVED_FIELDS})
            for entry in entries
        )
    except (TypeError, KeyError):
        return None
    return ScheduleList(schedules, SOURCE_SAVED, data["source"] == SOURCE_MQTT)


# Suffix of the quota key that holds the schedules; the prefix is the model's
TIMER_REPORT_SUFFIX = "_EMS_ALL_TIMER_TASK_REPORT"


def _flag(value: Any) -> bool:
    """Return a flag the cloud sends as a bool or as the text TRUE/FALSE."""
    if isinstance(value, str):
        return value.strip().upper() == "TRUE"
    return bool(value)


def _schedule_from_entry(entry: Any) -> Schedule | None:
    """Return the schedule of one entry of the list, None if it is incomplete."""
    if not isinstance(entry, dict):
        return None
    table = entry.get("timeTable")
    schedule_id = entry.get("taskIndex")
    schedule_type = entry.get("type")
    if (
        not isinstance(table, list)
        or not table
        or not isinstance(table[0], int)
        or not isinstance(schedule_id, int)
        or not isinstance(schedule_type, int)
    ):
        return None
    start, end = unpack_schedule_time(table[0])
    power = entry.get("sysChgDsgPwr")
    repeat = entry.get("timeMode")
    return Schedule(
        schedule_id=schedule_id,
        schedule_type=schedule_type,
        enabled=_flag(entry.get("isEnable")),
        power=int(power) if isinstance(power, (int, float)) else 0,
        start=start,
        end=end,
        repeat=repeat if isinstance(repeat, int) else 0,
        in_use=_flag(entry.get("isEffect")),
    )


def schedules_from_http(response: Mapping[str, Any]) -> list[Schedule] | None:
    """
    Return the schedules in a cloud HTTP response.

    None when the response has no schedule report (the list is unknown); an
    empty list when the report is empty. Entries without a time are skipped.
    """
    data = response.get("data")
    quota = data.get("quota") if isinstance(data, dict) else None
    if not isinstance(quota, dict):
        return None
    for key, report in quota.items():
        if not (isinstance(key, str) and key.endswith(TIMER_REPORT_SUFFIX)):
            continue
        if not isinstance(report, dict):
            return None
        entries = report.get("timeTaskCfg") or []
        schedules = (_schedule_from_entry(entry) for entry in entries)
        return [schedule for schedule in schedules if schedule is not None]
    return None


def _minutes(value: time) -> int:
    if value.second or value.microsecond:
        raise ScheduleInputError(KEY_INVALID_TIME)
    return value.hour * 60 + value.minute


def schedule_changes(data: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return the arguments of the coordinator for the input of an action.

    Only what was given is returned. `days` is a list of names (an empty list
    means daily); `daily` is the same as an empty list and excludes `days`.
    Raises ScheduleInputError for a power that is not whole watts, a time
    with seconds, or days together with daily.
    """
    changes: dict[str, Any] = {}
    if "type" in data:
        changes["schedule_type"] = TYPE_BY_KEY[data["type"]]
    if "power" in data:
        if not math.isfinite(float(data["power"])):
            raise ScheduleInputError(KEY_INVALID_POWER)
        if float(data["power"]) != int(data["power"]):
            raise ScheduleInputError(KEY_INVALID_POWER)
        changes["power"] = int(data["power"])
    for name in ("start", "end"):
        if name in data:
            changes[name] = _minutes(data[name])
    if data.get("daily"):
        if "days" in data:
            raise ScheduleInputError(KEY_DAYS_AND_DAILY)
        changes["days"] = 0
    elif "days" in data:
        changes["days"] = days_mask(data["days"])
    if "enabled" in data:
        changes["enabled"] = bool(data["enabled"])
    return changes


def _segments(start: int, end: int) -> list[tuple[int, int]]:
    """Return a window as segments within one day (two over midnight)."""
    if start < end:
        return [(start, end)]
    return [(start, DAY_MINUTES), (0, end)]


def windows_overlap(start1: int, end1: int, start2: int, end2: int) -> bool:
    """
    Return True if two daily windows share a minute.

    Minutes since midnight; a window may run over midnight (end before
    start). Back to back (one ends when the other starts) is no overlap, as
    in the app. The days are not looked at.
    """
    return any(
        first_start < second_end and second_start < first_end
        for first_start, first_end in _segments(start1, end1)
        for second_start, second_end in _segments(start2, end2)
    )
