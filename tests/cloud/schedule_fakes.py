"""A fake system that holds scheduled tasks, for the schedule tests.

Ported from powerocean_single_phase tests/integration/test_mqtt_schedules.py
(v1.0.1, 45287c9).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud.mqtt import (
    SCHEDULE_ADD,
    SCHEDULE_CHARGE,
    SCHEDULE_USE_BATTERY,
    Schedule,
)
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import FakeApi, model86_response
from tests.cloud.test_mqtt_link import FakeMqtt
from tests.cloud.test_settings import SettingsMqtt

MAX_POWER = 6000
COORDINATOR = "custom_components.ef_powerocean_hybrid.cloud.coordinator"
TIMEOUT = f"{COORDINATOR}.COMMAND_TIMEOUT"
DELAY = f"{COORDINATOR}.SCHEDULE_CONFIRM_DELAY"


def hm(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def listed(
    schedule_id: int,
    schedule_type: int,
    power: int,
    start: int,
    end: int,
    **changes: int | bool | None,
) -> Schedule:
    """Return a schedule as the system lists it (on, daily, not in use)."""
    values: dict[str, int | bool | None] = {
        "schedule_id": schedule_id,
        "schedule_type": schedule_type,
        "enabled": True,
        "power": power,
        "start": start,
        "end": end,
        "repeat": 1,
        "days": 0,
        "in_use": False,
        "unknown_2": 2,
    }
    return Schedule(**(values | changes))  # type: ignore[arg-type]


def system_schedules() -> list[Schedule]:
    return [
        listed(10, SCHEDULE_USE_BATTERY, 5000, hm(8, 30), hm(9), in_use=True),
        # Over midnight, with the repeat 65 of an older schedule
        listed(11, SCHEDULE_USE_BATTERY, 2000, hm(23), hm(2), repeat=65),
        listed(12, SCHEDULE_CHARGE, 1500, hm(6), hm(8, 30), enabled=False),
    ]


class ScheduleMqtt(SettingsMqtt):
    """The fake client with a system that holds schedules."""

    system: ClassVar[list[Schedule]] = []
    answer_lists: ClassVar[bool] = True
    # The next lists still show the state before the last command (the real
    # system may need a moment to show a change)
    stale_replies: ClassVar[int] = 0
    stale_system: ClassVar[list[Schedule]] = []

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.list_requests = 0
        self.commands: list[tuple[str, Any]] = []

    def request_schedules(self) -> None:
        """Answer with the schedules of the system (paho's thread)."""
        self.list_requests += 1
        if not ScheduleMqtt.answer_lists:
            return
        if ScheduleMqtt.stale_replies > 0:
            ScheduleMqtt.stale_replies -= 1
            self.callbacks.on_schedules(list(ScheduleMqtt.stale_system))
        else:
            self.callbacks.on_schedules(list(ScheduleMqtt.system))

    def _answer(
        self, seq: int, change: Callable[[list[Schedule]], list[Schedule]]
    ) -> None:
        if SettingsMqtt.acknowledge:
            ScheduleMqtt.stale_system = list(ScheduleMqtt.system)
            ScheduleMqtt.system = change(list(ScheduleMqtt.system))
            self.callbacks.on_ack(seq)

    def set_schedule_enabled(self, schedule_id: int, enabled: int, seq: int) -> None:
        self.commands.append(("switch", (schedule_id, enabled)))
        self._answer(
            seq,
            lambda schedules: [
                replace(s, enabled=bool(enabled)) if s.schedule_id == schedule_id else s
                for s in schedules
            ],
        )

    def save_schedule(self, action: int, schedule: Schedule, seq: int) -> None:
        self.commands.append(("add" if action == SCHEDULE_ADD else "edit", schedule))
        if action == SCHEDULE_ADD:
            self._answer(seq, lambda schedules: [*schedules, schedule])
        else:
            self._answer(
                seq,
                lambda schedules: [
                    replace(schedule, in_use=s.in_use, unknown_2=s.unknown_2)
                    if s.schedule_id == schedule.schedule_id
                    else s
                    for s in schedules
                ],
            )

    def delete_schedule(self, schedule_id: int, seq: int) -> None:
        self.commands.append(("delete", schedule_id))
        self._answer(
            seq,
            lambda schedules: [s for s in schedules if s.schedule_id != schedule_id],
        )


@contextmanager
def schedule_system() -> Iterator[None]:
    """The fake client with the system's schedules, fresh for each test."""
    FakeMqtt.instances.clear()
    SettingsMqtt.acknowledge = True
    ScheduleMqtt.system = system_schedules()
    ScheduleMqtt.answer_lists = True
    ScheduleMqtt.stale_replies = 0
    ScheduleMqtt.stale_system = system_schedules()
    with patch(f"{COORDINATOR}.PowerOceanMqtt", ScheduleMqtt):
        yield


async def setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    set_up: SetUp,
    *,
    max_power: int | None = MAX_POWER,
    mqtt: bool = True,
) -> MockConfigEntry:
    options: dict[str, Any] = {**entry.options, const.CONF_MQTT: mqtt}
    if max_power is not None:
        options[const.CONF_MAX_SCHEDULE_POWER] = max_power
    hass.config_entries.async_update_entry(entry, options=options)
    await set_up(entry, FakeApi(fetch=[model86_response()]))
    return entry


def client() -> ScheduleMqtt:
    (found,) = FakeMqtt.instances
    assert isinstance(found, ScheduleMqtt)
    return found


async def tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def key(error: pytest.ExceptionInfo[HomeAssistantError]) -> str | None:
    assert error.value.translation_domain == const.DOMAIN
    return error.value.translation_key
