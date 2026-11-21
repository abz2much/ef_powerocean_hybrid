"""The app's schedules offered through Battery Mode, the action and Control Status.

A hybrid entry (Modbus and cloud) offers them; a Modbus-only or cloud-only entry
behaves as before.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud.schedules import schedule_title
from custom_components.ef_powerocean_hybrid.models import ControlFeature
from custom_components.ef_powerocean_hybrid.services import SERVICE_SET_BATTERY_COMMAND
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL
from tests.cloud.schedule_fakes import client as _client
from tests.cloud.schedule_fakes import schedule_system, setup
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def fake_system() -> Iterator[None]:
    with schedule_system():
        yield


def _entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str
) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, const.DOMAIN, f"{entry.unique_id}_{key}"
    )
    assert entity_id is not None
    return entity_id


async def _switch_control_on(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": _entity_id(hass, entry, "switch", "modbus_control")},
        blocking=True,
    )
    await hass.async_block_till_done()
    await entry.runtime_data.modbus.async_refresh()
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _select(hass: HomeAssistant, entry: MockConfigEntry):
    state = hass.states.get(
        _entity_id(hass, entry, "select", const.BATTERY_MODE_SELECT.key)
    )
    assert state is not None
    return state


async def test_a_hybrid_entry_offers_schedules(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await setup(hass, hybrid_entry, set_up_hybrid)
    assert _select(hass, hybrid_entry).state == STATE_UNAVAILABLE

    await _switch_control_on(hass, hybrid_entry)

    state = _select(hass, hybrid_entry)
    assert const.SCHEDULES_OPTION in state.attributes["options"]
    assert state.state == const.SCHEDULES_OPTION


async def test_choosing_schedules_in_the_select_follows_them(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await setup(hass, hybrid_entry, set_up_hybrid)
    await _switch_control_on(hass, hybrid_entry)
    select_id = _entity_id(hass, hybrid_entry, "select", const.BATTERY_MODE_SELECT.key)
    control = hybrid_entry.runtime_data.modbus.control

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": "hold_battery"},
        blocking=True,
    )
    assert control.following_schedules is False
    assert _state(hass, select_id).state == "hold_battery"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": const.SCHEDULES_OPTION},
        blocking=True,
    )
    assert control.following_schedules is True
    assert _state(hass, select_id).state == const.SCHEDULES_OPTION


async def test_the_action_accepts_schedules(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await setup(hass, hybrid_entry, set_up_hybrid)
    await _switch_control_on(hass, hybrid_entry)
    control = hybrid_entry.runtime_data.modbus.control
    device = next(
        device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), hybrid_entry.entry_id
        )
        if (const.DOMAIN, SERIAL) in device.identifiers
    )
    await control.async_select_feature(ControlFeature.HOLD_BATTERY)
    assert control.following_schedules is False

    # The power is ignored, as are the limit and the expiry.
    await hass.services.async_call(
        const.DOMAIN,
        SERVICE_SET_BATTERY_COMMAND,
        {
            "device_id": device.id,
            "mode": "schedules",
            "power": 1234,
            "charge_limit_soc": 50,
            "expire_in": 120,
        },
        blocking=True,
    )

    assert control.following_schedules is True
    assert control.expires_at is None
    assert control.power != 1234


async def test_control_status_shows_the_slot(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await setup(hass, hybrid_entry, set_up_hybrid)
    await _switch_control_on(hass, hybrid_entry)
    control = hybrid_entry.runtime_data.modbus.control
    status_id = _entity_id(hass, hybrid_entry, "sensor", "control_status")

    attributes = _state(hass, status_id).attributes
    assert attributes["schedule_source"] == "mqtt"
    assert attributes["schedules_not_run"] == list(control.schedules_not_run)
    slot = control.schedule_slot
    expected = None if slot is None else schedule_title(slot)
    assert attributes["schedule"] == expected


async def test_control_status_shows_the_slot_title_while_one_runs(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    # 08:45 local: inside the system's 08:30-09:00 slot
    freezer.move_to(dt_util.now().replace(hour=8, minute=45, second=0))
    await setup(hass, hybrid_entry, set_up_hybrid)
    await _switch_control_on(hass, hybrid_entry)
    control = hybrid_entry.runtime_data.modbus.control
    slot = control.schedule_slot
    assert slot is not None
    status_id = _entity_id(hass, hybrid_entry, "sensor", "control_status")

    assert _state(hass, status_id).attributes["schedule"] == schedule_title(slot)


async def test_control_status_shows_that_schedules_drive(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await setup(hass, hybrid_entry, set_up_hybrid)
    await _switch_control_on(hass, hybrid_entry)
    status_id = _entity_id(hass, hybrid_entry, "sensor", "control_status")
    select_id = _entity_id(hass, hybrid_entry, "select", const.BATTERY_MODE_SELECT.key)
    assert _state(hass, status_id).attributes["following_schedules"] is True

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": "hold_battery"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _state(hass, status_id).attributes["following_schedules"] is False


@pytest.fixture
async def set_up_modbus_only(
    hass: HomeAssistant, inverter: FakeInverter
) -> AsyncIterator[Callable[[], Awaitable[MockConfigEntry]]]:
    entries: list[MockConfigEntry] = []

    async def set_up() -> MockConfigEntry:
        inverter.serve_serial(SERIAL, const.SERIAL_NUMBER.address)
        entry = MockConfigEntry(
            domain=const.DOMAIN,
            unique_id=SERIAL,
            minor_version=2,
            data={const.CONF_HOST: HOST, const.CONF_PORT: inverter.port},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entries.append(entry)
        return entry

    yield set_up
    for entry in entries:
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_modbus_only_entry_has_no_schedules_option(
    hass: HomeAssistant,
    set_up_modbus_only: Callable[[], Awaitable[MockConfigEntry]],
) -> None:
    entry = await set_up_modbus_only()
    await _switch_control_on(hass, entry)

    state = _select(hass, entry)
    assert const.SCHEDULES_OPTION not in state.attributes["options"]
    assert state.state == "automatic"
    control = entry.runtime_data.modbus.control
    assert control.schedules_available is False
    attributes = _state(
        hass, _entity_id(hass, entry, "sensor", "control_status")
    ).attributes
    assert attributes["schedule"] is None
    assert attributes["schedule_source"] is None
    assert attributes["schedules_not_run"] == []
    assert attributes["following_schedules"] is False


async def test_a_cloud_only_entry_is_unchanged(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await setup(hass, cloud_entry, set_up_hybrid)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "select", const.DOMAIN, f"{CLOUD_SN}_{const.BATTERY_MODE_SELECT.key}"
        )
        is None
    )
    assert (
        registry.async_get_entity_id(
            "sensor", const.DOMAIN, f"{CLOUD_SN}_control_status"
        )
        is None
    )
    assert cloud_entry.runtime_data.modbus is None
    assert _client().commands == []


async def test_the_action_refuses_schedules_on_a_modbus_only_entry(
    hass: HomeAssistant,
    set_up_modbus_only: Callable[[], Awaitable[MockConfigEntry]],
) -> None:
    entry = await set_up_modbus_only()
    await _switch_control_on(hass, entry)
    select_id = _entity_id(hass, entry, "select", const.BATTERY_MODE_SELECT.key)
    device = next(
        device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if (const.DOMAIN, SERIAL) in device.identifiers
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            const.DOMAIN,
            SERVICE_SET_BATTERY_COMMAND,
            {"device_id": device.id, "mode": "schedules"},
            blocking=True,
        )

    assert entry.runtime_data.modbus.control.following_schedules is False
    assert _state(hass, select_id).state == "automatic"
