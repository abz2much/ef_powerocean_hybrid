"""
schedule_runner.py: what the app's schedules say the battery should do now.

A pure function: given the schedule list and the time, it returns the control
to follow. The caller sends the command.

Rules:
- A slot is active from its start minute up to, not including, its end minute,
  in the local time zone. End 0 means midnight. Start equal to end is never
  active. A slot with its end before its start runs over midnight; its minutes
  after midnight belong to the day it started on.
- Switched-off slots never run.
- A slot that cannot be run (a repeat other than daily or chosen days, chosen
  days that are not trusted, a type other than charge or use battery, a start
  or end past 23:59) is skipped and listed in `not_run`, whatever the time.
- If two slots are active at once, the one with the lowest id wins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from homeassistant.util import dt as dt_util

from .cloud.mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_REPEAT_DAILY,
    SCHEDULE_REPEAT_DAYS,
    SCHEDULE_USE_BATTERY,
)
from .cloud.schedules import within_the_day
from .models import ControlFeature

if TYPE_CHECKING:
    from datetime import datetime

    from .cloud.mqtt import Schedule
    from .cloud.schedules import ScheduleList

MINUTES_PER_DAY = 24 * 60


class SlotCommand(NamedTuple):
    """What to do now: the control, its power in W, the slot behind it."""

    feature: ControlFeature
    power: float
    slot: Schedule | None
    # Ids of enabled slots that cannot be run, sorted
    not_run: tuple[int, ...]


def not_run_reason(slot: Schedule, days_trusted: bool) -> str | None:
    """Return why the runner cannot run this slot at all, None if it can."""
    if slot.schedule_type not in (SCHEDULE_CHARGE, SCHEDULE_USE_BATTERY):
        return "unknown type"
    if not within_the_day(slot):
        return "time past 23:59"
    if slot.repeat == SCHEDULE_REPEAT_DAILY:
        return None
    if slot.repeat != SCHEDULE_REPEAT_DAYS:
        return "unknown repeat"
    if not days_trusted:
        return "chosen days from a list without trusted days"
    return None


def _runnable(slot: Schedule, days_trusted: bool) -> bool:
    """Return whether the runner can run this slot at all."""
    return not_run_reason(slot, days_trusted) is None


def _runs_on(slot: Schedule, weekday: int) -> bool:
    """Return whether the slot runs on this weekday (Monday 0)."""
    if slot.repeat == SCHEDULE_REPEAT_DAILY:
        return True
    return bool(slot.days & (1 << weekday))


def _active(slot: Schedule, weekday: int, minute: int) -> bool:
    """Return whether an enabled, runnable slot is active at this minute."""
    if slot.start == slot.end:
        return False
    end = slot.end or MINUTES_PER_DAY
    if slot.start < end:
        return slot.start <= minute < end and _runs_on(slot, weekday)
    # Over midnight: the evening belongs to today, the morning to yesterday.
    if minute >= slot.start:
        return _runs_on(slot, weekday)
    return minute < end and _runs_on(slot, (weekday - 1) % 7)


def slot_command(schedules: ScheduleList | None, now: datetime) -> SlotCommand:
    """Return what the battery should do at `now`."""
    if schedules is None:
        return SlotCommand(ControlFeature.AUTOMATIC, 0.0, None, ())
    local = dt_util.as_local(now)
    minute = local.hour * 60 + local.minute
    weekday = local.weekday()
    enabled = [slot for slot in schedules.schedules if slot.enabled]
    not_run = tuple(
        sorted(
            slot.schedule_id
            for slot in enabled
            if not _runnable(slot, schedules.days_trusted)
        )
    )
    active = sorted(
        (
            slot
            for slot in enabled
            if _runnable(slot, schedules.days_trusted)
            and _active(slot, weekday, minute)
        ),
        key=lambda slot: slot.schedule_id,
    )
    if not active:
        return SlotCommand(ControlFeature.AUTOMATIC, 0.0, None, not_run)
    slot = active[0]
    if slot.schedule_type == SCHEDULE_CHARGE:
        return SlotCommand(
            ControlFeature.CHARGE_BATTERY, float(slot.power), slot, not_run
        )
    return SlotCommand(ControlFeature.AUTOMATIC, 0.0, slot, not_run)
