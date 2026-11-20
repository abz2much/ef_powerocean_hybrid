"""Bugs of the ported MQTT and schedule code, found in its review, fixed here."""

from __future__ import annotations

import math
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from custom_components.ef_powerocean_hybrid.cloud import mqtt
from custom_components.ef_powerocean_hybrid.cloud.schedules import (
    ScheduleInputError,
    schedule_changes,
)


def test_two_commands_in_the_same_millisecond_get_different_sequence_numbers() -> None:
    """A shared number would let one command's ack answer the other."""
    with patch("time.time", return_value=1_795_089_692.123):
        first, second = mqtt.new_seq(), mqtt.new_seq()

    assert first != second


@pytest.mark.parametrize("power", (math.nan, math.inf, -math.inf))
def test_a_power_that_is_not_a_number_is_refused(power: float) -> None:
    with pytest.raises(ScheduleInputError):
        schedule_changes({"power": power})


def _record(start: int, end: int) -> bytes:
    """A schedule as the system sends it: id 7, charge, on, 1000 W, daily."""
    time = mqtt._varint((end << 16) | start)
    return b"".join(
        (
            mqtt._field_varint(3, 7),
            mqtt._field_varint(4, 1),
            mqtt._field_varint(6, 1),
            mqtt._field_varint(7, 1000),
            mqtt._field_varint(8, 1),
            mqtt._field_bytes(10, time),
        )
    )


def test_a_schedule_with_a_time_past_midnight_is_read_as_sent() -> None:
    """The coordinator keeps it aside: it still holds its id on the system."""
    schedule = mqtt.parse_schedule(_record(60 * 2, 60 * 25))

    assert schedule is not None
    assert (schedule.start, schedule.end) == (60 * 2, 60 * 25)


def test_a_schedule_with_valid_times_is_read() -> None:
    schedule = mqtt.parse_schedule(_record(60 * 23, 60 * 2))

    assert schedule is not None
    assert (schedule.start, schedule.end) == (60 * 23, 60 * 2)


def test_sequence_numbers_stay_unique_across_threads() -> None:
    """Commands come from the event loop, list and stream requests from threads."""
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            batches = list(
                pool.map(lambda _: [mqtt.new_seq() for _ in range(5000)], range(8))
            )
    finally:
        sys.setswitchinterval(interval)
    numbers = [seq for batch in batches for seq in batch]

    assert len(set(numbers)) == len(numbers)
