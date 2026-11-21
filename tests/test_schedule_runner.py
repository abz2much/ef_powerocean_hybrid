"""Tests for working out which app schedule is active now."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.ef_powerocean_hybrid.cloud.mqtt import Schedule
from custom_components.ef_powerocean_hybrid.cloud.schedules import ScheduleList
from custom_components.ef_powerocean_hybrid.models import ControlFeature
from custom_components.ef_powerocean_hybrid.schedule_runner import (
    SlotCommand,
    slot_command,
)

MONDAY = 1
DUBLIN = "Europe/Dublin"


@pytest.fixture(autouse=True)
def dublin() -> Iterator[None]:
    """Use Europe/Dublin as the local time zone, then restore it."""
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(ZoneInfo(DUBLIN))
    yield
    dt_util.set_default_time_zone(previous)


def at(day: int, hour: int, minute: int = 0, month: int = 1) -> datetime:
    """Return a time in January 2026 (GMT, so UTC equals local) unless told."""
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


# 5 Jan 2026 is a Monday.
def mon(hour: int, minute: int = 0) -> datetime:
    return at(5, hour, minute)


def tue(hour: int, minute: int = 0) -> datetime:
    return at(6, hour, minute)


def sched(
    schedule_id: int = 1,
    *,
    schedule_type: int = 1,
    enabled: bool = True,
    power: int = 3000,
    start: int = 120,
    end: int = 360,
    repeat: int = 1,
    days: int = 0,
) -> Schedule:
    return Schedule(
        schedule_id=schedule_id,
        schedule_type=schedule_type,
        enabled=enabled,
        power=power,
        start=start,
        end=end,
        repeat=repeat,
        days=days,
    )


def mqtt(*schedules: Schedule) -> ScheduleList:
    return ScheduleList(tuple(schedules), "mqtt", True)


def test_charge_slot_active_inside_window() -> None:
    slot = sched()
    cmd = slot_command(mqtt(slot), mon(3))
    assert cmd == SlotCommand(ControlFeature.CHARGE_BATTERY, 3000.0, slot, ())
    assert isinstance(cmd.power, float)


def test_start_inclusive_end_exclusive() -> None:
    slot = sched()
    assert slot_command(mqtt(slot), mon(2)).slot is slot
    assert slot_command(mqtt(slot), mon(1, 59)).slot is None
    assert slot_command(mqtt(slot), mon(5, 59)).slot is slot
    assert slot_command(mqtt(slot), mon(6)).slot is None


def test_use_battery_over_midnight() -> None:
    slot = sched(schedule_type=2, start=23 * 60, end=120)
    for when in (mon(23, 30), tue(1, 30)):
        cmd = slot_command(mqtt(slot), when)
        assert cmd == SlotCommand(ControlFeature.AUTOMATIC, 0.0, slot, ())
    assert slot_command(mqtt(slot), mon(22, 59)).slot is None
    assert slot_command(mqtt(slot), tue(2)).slot is None


def test_gap_gives_automatic() -> None:
    cmd = slot_command(mqtt(sched()), mon(12))
    assert cmd == SlotCommand(ControlFeature.AUTOMATIC, 0.0, None, ())


def test_switched_off_never_active() -> None:
    assert slot_command(mqtt(sched(enabled=False)), mon(3)).slot is None


def test_chosen_days_over_midnight_belong_to_start_day() -> None:
    slot = sched(start=23 * 60, end=120, repeat=2, days=MONDAY)
    assert slot_command(mqtt(slot), mon(23, 30)).slot is slot
    assert slot_command(mqtt(slot), tue(1, 30)).slot is slot
    assert slot_command(mqtt(slot), tue(23, 30)).slot is None
    # Monday 01:30 is Sunday's night, and Sunday is not chosen.
    assert slot_command(mqtt(slot), mon(1, 30)).slot is None


def test_chosen_days_same_day() -> None:
    slot = sched(repeat=2, days=MONDAY)
    assert slot_command(mqtt(slot), mon(3)).slot is slot
    assert slot_command(mqtt(slot), tue(3)).slot is None


def test_sunday_bit() -> None:
    slot = sched(repeat=2, days=64)
    assert slot_command(mqtt(slot), at(11, 3)).slot is slot  # Sunday 11 Jan
    assert slot_command(mqtt(slot), at(5, 3)).slot is None


def test_end_zero_is_midnight() -> None:
    slot = sched(start=22 * 60, end=0)
    assert slot_command(mqtt(slot), mon(23, 59)).slot is slot
    assert slot_command(mqtt(slot), tue(0, 0)).slot is None


def test_start_equals_end_never_active() -> None:
    for start in (0, 120):
        slot = sched(start=start, end=start)
        for hour in (0, 2, 12, 23):
            assert slot_command(mqtt(slot), mon(hour)).slot is None


def test_unknown_repeat_not_run() -> None:
    slot = sched(7, repeat=65)
    cmd = slot_command(mqtt(slot), mon(3))
    assert cmd == SlotCommand(ControlFeature.AUTOMATIC, 0.0, None, (7,))


def test_unknown_type_is_automatic_and_not_run() -> None:
    slot = sched(8, schedule_type=3)
    cmd = slot_command(mqtt(slot), mon(3))
    assert cmd == SlotCommand(ControlFeature.AUTOMATIC, 0.0, None, (8,))


@pytest.mark.parametrize("source", ("http", "saved"))
def test_untrusted_days_not_run_for_chosen_days(source: str) -> None:
    slot = sched(4, repeat=2, days=MONDAY)
    cmd = slot_command(ScheduleList((slot,), source, False), mon(3))
    assert cmd == SlotCommand(ControlFeature.AUTOMATIC, 0.0, None, (4,))


def test_untrusted_days_still_run_daily() -> None:
    slot = sched(4)
    cmd = slot_command(ScheduleList((slot,), "http", False), mon(3))
    assert cmd.slot is slot
    assert cmd.not_run == ()


def test_not_run_lists_every_enabled_unrunnable_slot_sorted_any_time() -> None:
    slots = (
        sched(9, repeat=65),
        sched(3, schedule_type=5),
        sched(6, repeat=2, days=MONDAY),
        sched(2, repeat=65, enabled=False),
        sched(1, start=600, end=700),
    )
    cmd = slot_command(ScheduleList(slots, "http", False), mon(12))
    assert cmd.not_run == (3, 6, 9)
    assert cmd.slot is None


def test_lowest_id_wins_when_two_overlap() -> None:
    a = sched(5, power=1000)
    b = sched(2, schedule_type=2)
    cmd = slot_command(mqtt(a, b), mon(3))
    assert cmd.slot is b
    assert cmd.feature is ControlFeature.AUTOMATIC


def test_unrunnable_slot_does_not_block_a_runnable_one() -> None:
    bad = sched(1, repeat=65)
    good = sched(2)
    cmd = slot_command(mqtt(bad, good), mon(3))
    assert cmd.slot is good
    assert cmd.not_run == (1,)


def test_none_list() -> None:
    assert slot_command(None, mon(3)) == SlotCommand(
        ControlFeature.AUTOMATIC, 0.0, None, ()
    )


def test_local_time_not_utc_in_summer() -> None:
    slot = sched()
    # 01:30 UTC on 1 July is 02:30 in Dublin.
    assert slot_command(mqtt(slot), at(1, 1, 30, month=7)).slot is slot
    assert slot_command(mqtt(slot), at(1, 4, 30, month=7)).slot is slot
    assert slot_command(mqtt(slot), at(1, 5, 30, month=7)).slot is None
    assert slot_command(mqtt(slot), at(1, 0, 30, month=7)).slot is None


def test_summer_time_start_night() -> None:
    slot = sched()
    # 29 Mar 2026: 01:00 UTC is 02:00 local after the clocks go forward.
    assert (
        slot_command(mqtt(slot), datetime(2026, 3, 29, 1, 30, tzinfo=UTC)).slot is slot
    )
    assert (
        slot_command(mqtt(slot), datetime(2026, 3, 29, 5, 30, tzinfo=UTC)).slot is None
    )


def test_summer_time_end_night() -> None:
    slot = sched()
    # 25 Oct 2026: clocks go back at 01:00 UTC, so 02:30 UTC is 02:30 local.
    assert (
        slot_command(mqtt(slot), datetime(2026, 10, 25, 2, 30, tzinfo=UTC)).slot is slot
    )
    assert (
        slot_command(mqtt(slot), datetime(2026, 10, 25, 6, 30, tzinfo=UTC)).slot is None
    )
