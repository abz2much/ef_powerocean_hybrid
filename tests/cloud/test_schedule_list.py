"""The scheduled tasks in the cloud coordinator, with a fake system.

Ported from powerocean_single_phase tests/integration/test_mqtt_schedules.py
(v1.0.1, 45287c9), on a cloud-only entry: the schedules are the cloud's alone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from typing import Any, Protocol
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid.cloud.mqtt import (
    SCHEDULE_CHARGE,
    SCHEDULE_USE_BATTERY,
    Schedule,
)
from tests.cloud.conftest import SetUp
from tests.cloud.schedule_fakes import (
    DELAY,
    TIMEOUT,
    ScheduleMqtt,
    hm,
    listed,
    schedule_system,
    setup,
    system_schedules,
)
from tests.cloud.schedule_fakes import (
    client as _client,
)
from tests.cloud.schedule_fakes import (
    key as _key,
)
from tests.cloud.schedule_fakes import (
    tick as _tick,
)
from tests.cloud.test_mqtt_link import FakeMqtt
from tests.cloud.test_settings import SettingsMqtt

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class Start(Protocol):
    def __call__(
        self, *, max_power: int | None = ..., mqtt: bool = ...
    ) -> Awaitable[MockConfigEntry]: ...


@pytest.fixture(autouse=True)
def fake_system() -> Iterator[None]:
    with schedule_system():
        yield


@pytest.fixture
def set_up(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> Start:
    """Set up the cloud-only entry with MQTT and the max schedule power."""

    async def set_up(**options: Any) -> MockConfigEntry:
        return await setup(hass, cloud_entry, set_up_hybrid, **options)

    return set_up


# -----------------------------
# Reading
# -----------------------------


async def test_schedules_are_read_when_mqtt_starts(set_up: Start) -> None:
    entry = await set_up()
    coordinator = entry.runtime_data.cloud

    assert _client().list_requests == 1
    assert coordinator.schedules == {s.schedule_id: s for s in system_schedules()}
    assert coordinator.active_schedule == system_schedules()[0]


async def test_no_schedule_in_use(set_up: Start) -> None:
    ScheduleMqtt.system = [replace(s, in_use=False) for s in system_schedules()]

    entry = await set_up()

    assert entry.runtime_data.cloud.active_schedule is None


async def test_schedules_are_polled_every_60_seconds(
    hass: HomeAssistant, set_up: Start, freezer: FrozenDateTimeFactory
) -> None:
    await set_up()

    await _tick(hass, freezer, 59)
    assert _client().list_requests == 1

    await _tick(hass, freezer, 1)
    assert _client().list_requests == 2

    await _tick(hass, freezer, 60)
    assert _client().list_requests == 3


async def test_a_change_of_the_system_shows_with_the_next_poll(
    hass: HomeAssistant, set_up: Start, freezer: FrozenDateTimeFactory
) -> None:
    entry = await set_up()
    coordinator = entry.runtime_data.cloud
    # 08:30:22: the system moves "in use" from 10 to 11 by itself
    ScheduleMqtt.system = [
        replace(s, in_use=s.schedule_id == 11) for s in system_schedules()
    ]

    await _tick(hass, freezer, 60)

    assert coordinator.active_schedule is not None
    assert coordinator.active_schedule.schedule_id == 11


async def test_only_a_changed_list_notifies(hass: HomeAssistant, set_up: Start) -> None:
    entry = await set_up()
    notified: list[None] = []
    entry.runtime_data.cloud.async_add_listener(lambda: notified.append(None))
    callback = _client().callbacks.on_schedules

    await hass.async_add_executor_job(callback, system_schedules())
    await hass.async_block_till_done()
    assert notified == []

    changed = [replace(s, in_use=s.schedule_id == 11) for s in system_schedules()]
    await hass.async_add_executor_job(callback, changed)
    await hass.async_block_till_done()
    assert len(notified) == 1


async def test_the_list_of_the_app_is_taken_over(
    hass: HomeAssistant, set_up: Start
) -> None:
    entry = await set_up()
    coordinator = entry.runtime_data.cloud
    new = [listed(20, SCHEDULE_CHARGE, 1000, hm(1), hm(2))]

    await hass.async_add_executor_job(_client().callbacks.on_schedules, new)
    await hass.async_block_till_done()

    assert list(coordinator.schedules.values()) == new


async def test_a_single_schedule_of_the_app_is_taken_over(
    hass: HomeAssistant, set_up: Start
) -> None:
    entry = await set_up()
    coordinator = entry.runtime_data.cloud
    changed = replace(system_schedules()[0], power=4000)

    await hass.async_add_executor_job(_client().callbacks.on_schedule, changed)
    await hass.async_block_till_done()

    assert coordinator.schedules[10] == changed
    assert coordinator.schedules[11] == system_schedules()[1]


async def test_no_schedules_without_the_mqtt_option(set_up: Start) -> None:
    entry = await set_up(mqtt=False)

    assert not FakeMqtt.instances
    assert entry.runtime_data.cloud.schedules == {}
    with pytest.raises(HomeAssistantError) as error:
        await entry.runtime_data.cloud.async_set_schedule_enabled(12, enabled=True)
    assert _key(error) == "mqtt_not_connected"


async def test_unload_stops_the_poll_and_clears_the_schedules(
    hass: HomeAssistant, set_up: Start, freezer: FrozenDateTimeFactory
) -> None:
    entry = await set_up()
    coordinator = entry.runtime_data.cloud
    client = _client()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await _tick(hass, freezer, 120)

    assert client.list_requests == 1
    assert coordinator.schedules == {}


async def test_a_late_list_after_unload_is_ignored(
    hass: HomeAssistant, set_up: Start
) -> None:
    entry = await set_up()
    coordinator = entry.runtime_data.cloud
    client = _client()
    assert await hass.config_entries.async_unload(entry.entry_id)

    await hass.async_add_executor_job(client.callbacks.on_schedules, system_schedules())
    await hass.async_block_till_done()

    assert coordinator.schedules == {}


# -----------------------------
# Switching and deleting
# -----------------------------


async def test_switch_a_schedule_on_and_off(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_set_schedule_enabled(12, enabled=True)

    assert _client().commands == [("switch", (12, 1))]
    assert coordinator.schedules[12].enabled is True
    # The list is read again after the command
    assert _client().list_requests == 2

    await coordinator.async_set_schedule_enabled(12, enabled=False)

    assert _client().commands[-1] == ("switch", (12, 0))
    assert coordinator.schedules[12].enabled is False


async def test_switching_needs_no_max_power_option(set_up: Start) -> None:
    coordinator = (await set_up(max_power=None)).runtime_data.cloud

    await coordinator.async_set_schedule_enabled(12, enabled=True)
    await coordinator.async_delete_schedule(11)

    assert [name for name, _ in _client().commands] == ["switch", "delete"]


async def test_delete_a_schedule(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_delete_schedule(11)

    assert _client().commands == [("delete", 11)]
    assert list(coordinator.schedules) == [10, 12]


@pytest.mark.parametrize(
    "command",
    (
        lambda c: c.async_set_schedule_enabled(99, enabled=True),
        lambda c: c.async_delete_schedule(99),
        lambda c: c.async_update_schedule(99, power=1000),
    ),
    ids=["switch", "delete", "update"],
)
async def test_an_unknown_schedule_is_refused(
    set_up: Start, command: Callable[[Any], Any]
) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await command(coordinator)

    assert _key(error) == "schedule_not_found"
    assert error.value.translation_placeholders == {"schedule_id": "99"}
    assert _client().commands == []


async def test_a_command_without_acknowledgement_fails_and_changes_nothing(
    set_up: Start,
) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    SettingsMqtt.acknowledge = False

    with patch(TIMEOUT, 0.01), pytest.raises(HomeAssistantError) as error:
        await coordinator.async_set_schedule_enabled(12, enabled=True)

    assert _key(error) == "not_acknowledged"
    assert coordinator.schedules[12].enabled is False
    # No new reading after a command that was not acknowledged
    assert _client().list_requests == 1


async def test_a_command_the_list_does_not_confirm_is_reported(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    ScheduleMqtt.answer_lists = False

    with (
        patch(TIMEOUT, 0.05),
        patch(DELAY, 0),
        pytest.raises(HomeAssistantError) as error,
    ):
        await coordinator.async_set_schedule_enabled(12, enabled=True)

    # The command was acknowledged, but nothing shows that it took effect
    assert _key(error) == "schedule_not_confirmed"
    assert _client().commands == [("switch", (12, 1))]
    assert coordinator.schedules[12].enabled is False


# -----------------------------
# Adding
# -----------------------------


async def test_add_takes_the_next_free_id(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    new_id = await coordinator.async_add_schedule(
        schedule_type=SCHEDULE_CHARGE, power=2500, start=hm(2), end=hm(5)
    )

    assert new_id == 13
    assert _client().commands == [
        (
            "add",
            listed(13, SCHEDULE_CHARGE, 2500, hm(2), hm(5), unknown_2=None),
        )
    ]
    assert coordinator.schedules[13].power == 2500


async def test_add_with_chosen_days_and_switched_off(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_add_schedule(
        schedule_type=SCHEDULE_USE_BATTERY,
        power=3000,
        start=hm(10),
        end=hm(11),
        days=1 + 2 + 4 + 8 + 16,
        enabled=False,
    )

    ((name, sent),) = _client().commands
    assert name == "add"
    assert (sent.enabled, sent.repeat, sent.days) == (False, 2, 31)
    assert (sent.start, sent.end) == (hm(10), hm(11))


async def test_two_adds_at_once_get_different_ids(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    windows = [(hm(10), hm(11)), (hm(12), hm(13))]
    ids = await asyncio.gather(
        *(
            coordinator.async_add_schedule(
                schedule_type=SCHEDULE_CHARGE, power=1000, start=start, end=end
            )
            for start, end in windows
        )
    )

    assert sorted(ids) == [13, 14]
    assert sorted(coordinator.schedules) == [10, 11, 12, 13, 14]


async def test_add_is_refused_before_the_list_is_known(set_up: Start) -> None:
    ScheduleMqtt.answer_lists = False
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(1), end=hm(2)
        )

    assert _key(error) == "schedules_unknown"
    assert _client().commands == []


@pytest.mark.parametrize(
    ("changes", "key"),
    (
        ({"power": 6001}, "schedule_power_too_high"),
        ({"power": 0}, "schedule_invalid_power"),
        ({"start": hm(1, 15)}, "schedule_invalid_time"),
        ({"end": hm(1)}, "schedule_invalid_time"),
        ({"schedule_type": 3}, "schedule_invalid_type"),
        ({"days": 128}, "schedule_invalid_days"),
    ),
)
async def test_add_is_refused_for_invalid_values(
    set_up: Start, changes: dict[str, int], key: str
) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    values = {
        "schedule_type": SCHEDULE_CHARGE,
        "power": 1000,
        "start": hm(1),
        "end": hm(2),
    }

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(**(values | changes))

    assert _key(error) == key
    assert _client().commands == []


async def test_add_and_update_are_refused_without_the_max_power_option(
    set_up: Start,
) -> None:
    coordinator = (await set_up(max_power=None)).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as add_error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(1), end=hm(2)
        )
    with pytest.raises(HomeAssistantError) as update_error:
        await coordinator.async_update_schedule(12, end=hm(9))

    assert _key(add_error) == _key(update_error) == "max_schedule_power_not_set"
    assert _client().commands == []


# -----------------------------
# Updating
# -----------------------------


async def test_update_sends_the_whole_schedule_and_keeps_the_rest(
    set_up: Start,
) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_update_schedule(11, power=2500)

    # Repeat 65 of the older schedule is sent back unchanged
    assert _client().commands == [
        (
            "edit",
            listed(11, SCHEDULE_USE_BATTERY, 2500, hm(23), hm(2), repeat=65),
        )
    ]
    assert coordinator.schedules[11].power == 2500


async def test_update_time_type_and_enabled(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_update_schedule(
        12,
        schedule_type=SCHEDULE_USE_BATTERY,
        start=hm(9),
        end=hm(11, 30),
        enabled=True,
    )

    ((_, sent),) = _client().commands
    assert (sent.schedule_type, sent.start, sent.end, sent.enabled) == (
        SCHEDULE_USE_BATTERY,
        hm(9),
        hm(11, 30),
        True,
    )
    assert (sent.power, sent.repeat, sent.days) == (1500, 1, 0)


@pytest.mark.parametrize(
    ("days", "expected"), ((96, (2, 96)), (127, (2, 127)), (0, (1, 0)))
)
async def test_update_days(set_up: Start, days: int, expected: tuple[int, int]) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_update_schedule(12, days=days)

    ((_, sent),) = _client().commands
    assert (sent.repeat, sent.days) == expected


async def test_update_only_checks_the_power_when_it_changes(set_up: Start) -> None:
    # The option was lowered below the 5000 W of schedule 10
    coordinator = (await set_up(max_power=3000)).runtime_data.cloud

    await coordinator.async_update_schedule(10, end=hm(10))
    assert coordinator.schedules[10].end == hm(10)

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_update_schedule(10, power=3500)
    assert _key(error) == "schedule_power_too_high"


async def test_update_is_refused_for_invalid_values(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_update_schedule(12, start=hm(6, 10))

    assert _key(error) == "schedule_invalid_time"
    assert _client().commands == []


# -----------------------------
# No overlaps, and no more schedules than the app allows
# -----------------------------


def _free_schedules(count: int) -> list[Schedule]:
    """Return `count` back-to-back one-hour charge schedules from 10:00."""
    return [
        listed(10 + n, SCHEDULE_CHARGE, 1000, hm(10 + n), hm(11 + n))
        for n in range(count)
    ]


@pytest.mark.parametrize(
    ("start", "end"),
    (
        (hm(8), hm(10)),  # into 10 (08:30-09:00) and the end of 12
        (hm(5), hm(9, 30)),  # around 12 and 10
        (hm(7), hm(8)),  # inside 12, which is switched off: it still counts
        (hm(1), hm(3)),  # over the end of 11 (23:00-02:00)
        (hm(20), hm(23, 30)),  # over the start of 11
        (hm(23), hm(2)),  # the same as 11
    ),
)
async def test_add_is_refused_when_it_overlaps(
    set_up: Start, start: int, end: int
) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=start, end=end
        )

    assert _key(error) == "schedule_overlap"
    assert _client().commands == []


async def test_the_error_names_the_schedule_in_the_way(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(8), end=hm(10)
        )

    assert error.value.translation_placeholders == {
        "other": "Use battery 08:30-09:00 5000 W"
    }


async def test_the_days_make_no_difference_to_an_overlap(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE,
            power=1000,
            start=hm(8),
            end=hm(10),
            days=1,
        )

    assert _key(error) == "schedule_overlap"


@pytest.mark.parametrize(
    ("start", "end"),
    (
        (hm(9), hm(10)),  # starts when 10 ends
        (hm(2), hm(6)),  # between the end of 11 and the start of 12
        (hm(22), hm(23)),  # ends when 11 starts
    ),
)
async def test_back_to_back_is_fine(set_up: Start, start: int, end: int) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_add_schedule(
        schedule_type=SCHEDULE_CHARGE, power=1000, start=start, end=end
    )

    assert len(_client().commands) == 1


async def test_update_is_refused_when_the_new_time_overlaps(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_update_schedule(12, end=hm(9))  # into 10

    assert _key(error) == "schedule_overlap"
    assert _client().commands == []


async def test_update_does_not_overlap_itself(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_update_schedule(12, start=hm(6), end=hm(8, 30))

    assert len(_client().commands) == 1


async def test_update_without_a_new_time_ignores_an_old_overlap(set_up: Start) -> None:
    ScheduleMqtt.system = [
        listed(10, SCHEDULE_CHARGE, 1000, hm(8), hm(10)),
        listed(11, SCHEDULE_CHARGE, 1000, hm(9), hm(11)),
    ]
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_update_schedule(10, power=1500)
    assert coordinator.schedules[10].power == 1500

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_update_schedule(10, end=hm(9, 30))
    assert _key(error) == "schedule_overlap"


async def test_the_seventh_schedule_is_refused(set_up: Start) -> None:
    ScheduleMqtt.system = _free_schedules(6)
    coordinator = (await set_up()).runtime_data.cloud

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(17), end=hm(18)
        )

    assert _key(error) == "schedule_limit_reached"
    assert error.value.translation_placeholders == {"max": "6"}
    assert _client().commands == []


async def test_the_sixth_schedule_is_accepted(set_up: Start) -> None:
    ScheduleMqtt.system = _free_schedules(5)
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_add_schedule(
        schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(17), end=hm(18)
    )

    assert len(coordinator.schedules) == 6


async def test_at_the_limit_update_and_delete_still_work(set_up: Start) -> None:
    ScheduleMqtt.system = _free_schedules(6)
    coordinator = (await set_up()).runtime_data.cloud

    await coordinator.async_update_schedule(10, power=500)
    await coordinator.async_delete_schedule(10)

    assert len(coordinator.schedules) == 5


# -----------------------------
# The system may need a moment to show a change
# -----------------------------


async def test_an_add_waits_until_the_list_shows_it(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    ScheduleMqtt.stale_replies = 2

    with patch(DELAY, 0):
        new_id = await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(10), end=hm(11)
        )

    assert coordinator.schedules[new_id].start == hm(10)
    # The start, two lists that did not show it yet, and the one that did
    assert _client().list_requests == 4


async def test_two_adds_in_a_row_get_different_ids_with_a_stale_list(
    set_up: Start,
) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    ScheduleMqtt.stale_replies = 3
    windows = [(hm(10), hm(11)), (hm(12), hm(13))]

    with patch(DELAY, 0):
        ids = await asyncio.gather(
            *(
                coordinator.async_add_schedule(
                    schedule_type=SCHEDULE_CHARGE, power=1000, start=start, end=end
                )
                for start, end in windows
            )
        )

    assert sorted(ids) == [13, 14]
    assert sorted(coordinator.schedules) == [10, 11, 12, 13, 14]


async def test_a_stale_list_does_not_hide_an_overlap(set_up: Start) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    ScheduleMqtt.stale_replies = 3

    with patch(DELAY, 0):
        results = await asyncio.gather(
            coordinator.async_add_schedule(
                schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(10), end=hm(12)
            ),
            coordinator.async_add_schedule(
                schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(11), end=hm(13)
            ),
            return_exceptions=True,
        )

    assert results[0] == 13
    assert isinstance(results[1], HomeAssistantError)
    assert results[1].translation_key == "schedule_overlap"


async def test_an_id_is_not_used_again_while_the_list_does_not_show_it(
    set_up: Start,
) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    ScheduleMqtt.stale_replies = 1000

    with (
        patch(TIMEOUT, 0.05),
        patch(DELAY, 0),
        pytest.raises(HomeAssistantError) as error,
    ):
        await coordinator.async_add_schedule(
            schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(10), end=hm(11)
        )
    assert _key(error) == "schedule_not_confirmed"

    # The system has it as 13; the list may show it late. The next one is 14
    ScheduleMqtt.stale_replies = 0
    await coordinator.async_add_schedule(
        schedule_type=SCHEDULE_CHARGE, power=1000, start=hm(12), end=hm(13)
    )

    ids = [schedule.schedule_id for _, schedule in _client().commands]
    assert ids == [13, 14]


@pytest.mark.parametrize(
    ("command", "shown"),
    (
        (
            lambda c: c.async_set_schedule_enabled(12, enabled=True),
            lambda c: c.schedules[12].enabled is True,
        ),
        (
            lambda c: c.async_delete_schedule(11),
            lambda c: 11 not in c.schedules,
        ),
        (
            lambda c: c.async_update_schedule(10, power=1234),
            lambda c: c.schedules[10].power == 1234,
        ),
    ),
    ids=["switch", "delete", "update"],
)
async def test_every_command_waits_until_the_list_shows_it(
    set_up: Start,
    command: Callable[[Any], Any],
    shown: Callable[[Any], bool],
) -> None:
    coordinator = (await set_up()).runtime_data.cloud
    ScheduleMqtt.stale_replies = 2

    with patch(DELAY, 0):
        await command(coordinator)

    # Back from the command, the list already shows it: after the start, two
    # lists that did not and the one that did
    assert shown(coordinator)
    assert _client().list_requests == 4
