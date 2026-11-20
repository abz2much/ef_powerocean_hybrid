"""What a schedule may be before it is sent to the system.

From powerocean_single_phase v1.0.1 (45287c9), MIT, (c) 2026 niltrip, abz2much.
"""

from __future__ import annotations

from typing import NoReturn

from homeassistant.exceptions import ServiceValidationError

from ..const import DOMAIN
from .mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_REPEAT_DAILY,
    SCHEDULE_REPEAT_DAYS,
    SCHEDULE_USE_BATTERY,
    Schedule,
)

SCHEDULE_TYPES = (SCHEDULE_CHARGE, SCHEDULE_USE_BATTERY)
# The app allows six schedules; more are refused.
MAX_SCHEDULES = 6
# Schedules start and end on the half hour.
SCHEDULE_STEP = 30
SCHEDULE_LAST_MINUTE = 23 * 60 + 30
# Monday to Sunday as a bit mask.
ALL_DAYS = 127


def refuse(key: str, **placeholders: int | str) -> NoReturn:
    """Raise the error for a refused schedule command."""
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={
            name: str(value) for name, value in placeholders.items()
        }
        or None,
    )


def validate_schedule(
    schedule: Schedule,
    max_power: int | None,
    *,
    check_power: bool = True,
    check_days: bool = True,
) -> None:
    """Raise if a schedule may not be sent.

    The type must be one of the app's two. Without the option "Max schedule
    power (W)" nothing is accepted; the power is a whole number from 1 to the
    option. Start and end are different times in steps of 30 minutes from
    00:00 to 23:30 (the end may be before the start: over midnight). The days
    are daily (repeat 1, days 0) or chosen (repeat 2, a mask from 1 to 127).
    Overlaps and the number of schedules are checked against the list.
    """
    if schedule.schedule_type not in SCHEDULE_TYPES:
        refuse("schedule_invalid_type")
    if max_power is None:
        refuse("max_schedule_power_not_set")
    if check_power:
        if schedule.power < 1:
            refuse("schedule_invalid_power")
        if schedule.power > max_power:
            refuse("schedule_power_too_high", power=schedule.power, max_power=max_power)
    times = (schedule.start, schedule.end)
    if schedule.start == schedule.end or any(
        not 0 <= minute <= SCHEDULE_LAST_MINUTE or minute % SCHEDULE_STEP
        for minute in times
    ):
        refuse("schedule_invalid_time")
    daily = (schedule.repeat, schedule.days) == (SCHEDULE_REPEAT_DAILY, 0)
    chosen = schedule.repeat == SCHEDULE_REPEAT_DAYS and 1 <= schedule.days <= ALL_DAYS
    if check_days and not (daily or chosen):
        refuse("schedule_invalid_days")
