"""The MQTT connection, as the cloud integration runs it (coordinator.py:306-375).

Ported from powerocean_single_phase tests/integration/test_mqtt.py.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud.api import EcoflowConnectionError
from custom_components.ef_powerocean_hybrid.cloud.mqtt import (
    ModuleVersion,
    MqttCallbacks,
    module_versions,
)
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import (
    CLOUD_SN,
    CREDENTIALS,
    SERIAL,
    USER_ID,
    FakeApi,
    model86_response,
)
from tests.fake_inverter import FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FIXTURES = Path(__file__).parent / "fixtures"
# get_reply with the module info, see test_mqtt.py
MODULES = cast(
    dict[int, ModuleVersion],
    module_versions(bytes.fromhex((FIXTURES / "mqtt_module_info.hex").read_text())),
)
METER = f"{CLOUD_SN}_meter106"


class FakeMqtt:
    """Stands in for PowerOceanMqtt: no network, records the calls."""

    instances: ClassVar[list[FakeMqtt]] = []

    def __init__(
        self,
        credentials: dict[str, Any],
        serial: str,
        user_id: str,
        callbacks: MqttCallbacks,
    ) -> None:
        self.credentials = credentials
        self.serial = serial
        self.user_id = user_id
        self.callbacks = callbacks
        self.started = self.stopped = False
        self.requests = 0
        self.schedule_list_requests = 0
        FakeMqtt.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def request_full_state(self) -> None:
        self.requests += 1

    def request_schedules(self) -> None:
        self.schedule_list_requests += 1


@pytest.fixture(autouse=True)
def fake_mqtt() -> Iterator[None]:
    FakeMqtt.instances.clear()
    with patch(
        "custom_components.ef_powerocean_hybrid.cloud.coordinator.PowerOceanMqtt",
        FakeMqtt,
    ):
        yield


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    set_up: SetUp,
    *,
    mqtt: bool | None,
    api: FakeApi | None = None,
) -> FakeApi:
    if mqtt is not None:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, const.CONF_MQTT: mqtt}
        )
    api = api or FakeApi(fetch=[model86_response()])
    await set_up(entry, api)
    return api


def _meter_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(const.DOMAIN, METER)}
    )


@pytest.mark.parametrize("mqtt", (None, False))
async def test_off_by_default(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    mqtt: bool | None,
) -> None:
    api = await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=mqtt)

    assert hybrid_entry.state is ConfigEntryState.LOADED
    assert api.credential_requests == 0
    assert not FakeMqtt.instances
    assert hybrid_entry.runtime_data.cloud.mqtt is None


async def test_started_with_the_accounts_credentials_and_stopped_on_unload(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True)
    cloud = hybrid_entry.runtime_data.cloud
    (client,) = FakeMqtt.instances

    assert client.started
    assert (client.credentials, client.serial, client.user_id) == (
        CREDENTIALS,
        CLOUD_SN,
        USER_ID,
    )

    assert await hass.config_entries.async_unload(hybrid_entry.entry_id)
    await hass.async_block_till_done()
    assert client.stopped
    assert cloud.mqtt is None


async def test_module_firmware_is_kept(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True)
    (client,) = FakeMqtt.instances

    # paho calls back from its own thread
    await hass.async_add_executor_job(client.callbacks.on_modules, MODULES)
    await hass.async_block_till_done()

    assert hybrid_entry.runtime_data.cloud.modules == MODULES


async def test_meter_firmware_only_on_an_existing_meter_device(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    """As the cloud integration: no meter device is made for the firmware."""
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True)
    (client,) = FakeMqtt.instances
    await hass.async_add_executor_job(client.callbacks.on_modules, MODULES)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    assert not [d for d in registry.devices if (const.DOMAIN, METER) in d.identifiers]

    meter = _meter_device(hass, hybrid_entry)
    await hass.async_add_executor_job(client.callbacks.on_modules, MODULES)
    await hass.async_block_till_done()

    updated = cast(dr.DeviceEntry, registry.async_get(meter.id))
    assert updated is not None
    assert updated.sw_version == MODULES[106].firmware
    # The HTTP poll does not overwrite it
    await hybrid_entry.runtime_data.cloud.async_refresh()
    updated = cast(dr.DeviceEntry, registry.async_get(meter.id))
    assert updated is not None
    assert updated.sw_version == MODULES[106].firmware


async def test_switched_off_removes_the_meter_firmware(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True)
    (client,) = FakeMqtt.instances
    meter = _meter_device(hass, hybrid_entry)
    await hass.async_add_executor_job(client.callbacks.on_modules, MODULES)
    await hass.async_block_till_done()

    # As the options flow does: save, then reload
    hass.config_entries.async_update_entry(
        hybrid_entry, options={**hybrid_entry.options, const.CONF_MQTT: False}
    )
    await hass.async_block_till_done()

    assert client.stopped
    assert len(FakeMqtt.instances) == 1
    updated = cast(dr.DeviceEntry, dr.async_get(hass).async_get(meter.id))
    assert updated is not None
    assert updated.sw_version is None


async def test_full_state_requested_again(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True)
    (client,) = FakeMqtt.instances

    freezer.tick(timedelta(hours=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert client.requests == 1


async def test_late_message_after_unload_is_ignored(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True)
    cloud = hybrid_entry.runtime_data.cloud
    (client,) = FakeMqtt.instances
    assert await hass.config_entries.async_unload(hybrid_entry.entry_id)

    await hass.async_add_executor_job(client.callbacks.on_modules, MODULES)
    await hass.async_block_till_done()

    assert cloud.modules == {}


@pytest.mark.parametrize("where", ("credentials", "start"))
async def test_start_failure_keeps_the_integration_running(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    caplog: pytest.LogCaptureFixture,
    where: str,
) -> None:
    """Not retried, as in the cloud integration: MQTT needs a reload."""
    api = FakeApi(fetch=[model86_response()])
    if where == "credentials":
        api.mqtt_credentials = EcoflowConnectionError("offline")
        await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True, api=api)
    else:
        with patch.object(FakeMqtt, "start", side_effect=OSError("certificates")):
            await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True, api=api)

    assert hybrid_entry.state is ConfigEntryState.LOADED
    assert hybrid_entry.runtime_data.cloud.mqtt is None
    assert "MQTT not started" in caplog.text


async def test_no_mqtt_without_user_id(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[model86_response()])
    api.login_user_id = None

    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=True, api=api)

    assert hybrid_entry.state is ConfigEntryState.LOADED
    assert api.credential_requests == 0
    assert not FakeMqtt.instances


async def _cloud_down_at_start(
    hass: HomeAssistant, entry: MockConfigEntry, set_up: SetUp
) -> FakeApi:
    api = FakeApi(login=EcoflowConnectionError("offline"), fetch=[model86_response()])
    await _setup(hass, entry, set_up, mqtt=True, api=api)
    return api


async def test_cloud_down_at_start_starts_mqtt_with_its_first_answer(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cloud integration retries its setup until the cloud answers
    (__init__.py:88) and starts MQTT then (:111). This entry loads without the
    cloud, so MQTT starts with the cloud's first answer instead."""
    api = await _cloud_down_at_start(hass, hybrid_entry, set_up_hybrid)
    cloud = hybrid_entry.runtime_data.cloud
    assert hybrid_entry.state is ConfigEntryState.LOADED
    assert not FakeMqtt.instances
    assert "MQTT not started" not in caplog.text

    api.recover()
    await cloud.async_refresh()
    await hass.async_block_till_done()

    (client,) = FakeMqtt.instances
    assert client.started
    assert (client.credentials, client.serial, client.user_id) == (
        CREDENTIALS,
        CLOUD_SN,
        USER_ID,
    )
    assert cloud.mqtt is client


async def test_no_late_start_while_the_cloud_is_still_down(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    """Guard: a coordinator does not call its listeners while polls keep failing."""
    api = await _cloud_down_at_start(hass, hybrid_entry, set_up_hybrid)
    cloud = hybrid_entry.runtime_data.cloud

    await cloud.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not FakeMqtt.instances
    assert api.credential_requests == 0

    # And it still starts once the cloud answers
    api.recover()
    await cloud.async_refresh()
    await hass.async_block_till_done()
    assert len(FakeMqtt.instances) == 1


async def test_mqtt_started_with_the_cloud_only_once(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = await _cloud_down_at_start(hass, hybrid_entry, set_up_hybrid)
    cloud = hybrid_entry.runtime_data.cloud
    api.recover()

    for _ in range(3):
        await cloud.async_refresh()
        await hass.async_block_till_done()

    assert len(FakeMqtt.instances) == 1
    assert api.credential_requests == 1


async def test_a_late_start_that_fails_is_not_retried(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """As at setup in the cloud integration: a failed start needs a reload."""
    api = await _cloud_down_at_start(hass, hybrid_entry, set_up_hybrid)
    cloud = hybrid_entry.runtime_data.cloud
    api.mqtt_credentials = EcoflowConnectionError("offline")
    api.recover()

    for _ in range(2):
        await cloud.async_refresh()
        await hass.async_block_till_done()

    assert cloud.mqtt is None
    assert api.credential_requests == 1
    assert "MQTT not started" in caplog.text


async def test_no_late_start_after_unload(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    """Guard: Home Assistant shuts the cloud coordinator down on unload."""
    api = await _cloud_down_at_start(hass, hybrid_entry, set_up_hybrid)
    cloud = hybrid_entry.runtime_data.cloud
    assert await hass.config_entries.async_unload(hybrid_entry.entry_id)
    await hass.async_block_till_done()

    api.recover()
    await cloud.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not FakeMqtt.instances
    assert api.credential_requests == 0


async def test_no_late_start_with_mqtt_off(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(login=EcoflowConnectionError("offline"), fetch=[model86_response()])
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=False, api=api)
    cloud = hybrid_entry.runtime_data.cloud

    api.recover()
    await cloud.async_refresh()
    await hass.async_block_till_done()

    assert not FakeMqtt.instances


# 6.49.91.20 in register 40012, low word first.
MODBUS_FIRMWARE = (0x5B14, 0x0631)


@pytest.mark.parametrize("started_with_mqtt", (False, True))
async def test_mqtt_never_clears_the_modbus_inverter_firmware(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
    started_with_mqtt: bool,
) -> None:
    """The cloud integration clears the inverter's firmware too
    (coordinator.py:402-414); with Modbus it is the Modbus value."""
    address = const.FIRMWARE_VERSION.address
    inverter.registers[address], inverter.registers[address + 1] = MODBUS_FIRMWARE
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=started_with_mqtt)
    if started_with_mqtt:
        (client,) = FakeMqtt.instances
        await hass.async_add_executor_job(client.callbacks.on_modules, MODULES)
        await hass.async_block_till_done()
        # Switched off later: the options flow saves, then the entry reloads
        hass.config_entries.async_update_entry(
            hybrid_entry, options={**hybrid_entry.options, const.CONF_MQTT: False}
        )
        await hass.async_block_till_done()

    (device,) = (
        device
        for device in dr.async_get(hass).devices
        if (const.DOMAIN, SERIAL) in device.identifiers
    )
    assert device.sw_version == "6.49.91.20"


# Review fix: an unload while a late start runs (fix B) runs the stop first,
# then cancels the start. Nothing of the start may outlive the entry.


class SlowMqtt(FakeMqtt):
    """A client whose start, or first list request, waits to be let go."""

    hold: ClassVar[str] = "start"
    entered: ClassVar[threading.Event] = threading.Event()
    release: ClassVar[threading.Event] = threading.Event()

    def start(self) -> None:
        if SlowMqtt.hold == "start":
            SlowMqtt.entered.set()
            SlowMqtt.release.wait(5)
        super().start()

    def request_schedules(self) -> None:
        if SlowMqtt.hold == "list":
            SlowMqtt.entered.set()
            SlowMqtt.release.wait(5)
        super().request_schedules()


async def _cancel_while_held(
    hass: HomeAssistant, entry: MockConfigEntry, set_up: SetUp, hold: str
) -> SlowMqtt:
    """Start MQTT, stop it and cancel the start while *hold* is running."""
    await _setup(hass, entry, set_up, mqtt=False)
    cloud = entry.runtime_data.cloud
    SlowMqtt.hold = hold
    SlowMqtt.entered.clear()
    SlowMqtt.release.clear()
    with patch(
        "custom_components.ef_powerocean_hybrid.cloud.coordinator.PowerOceanMqtt",
        SlowMqtt,
    ):
        task = hass.async_create_task(cloud.async_start_mqtt())
        assert await hass.async_add_executor_job(SlowMqtt.entered.wait, 5)
        # The unload: the stop first, then the background task is cancelled.
        await cloud.async_stop_mqtt()
        task.cancel()
        SlowMqtt.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await hass.async_block_till_done(wait_background_tasks=True)
    (client,) = FakeMqtt.instances
    assert isinstance(client, SlowMqtt)
    return client


async def test_a_start_cancelled_while_connecting_stops_the_client(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    client = await _cancel_while_held(hass, hybrid_entry, set_up_hybrid, "start")

    assert client.started
    assert client.stopped
    assert hybrid_entry.runtime_data.cloud.mqtt is None


async def test_a_start_cancelled_after_connecting_leaves_no_timers(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    client = await _cancel_while_held(hass, hybrid_entry, set_up_hybrid, "list")
    requests = client.schedule_list_requests

    freezer.tick(timedelta(hours=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert client.stopped
    assert hybrid_entry.runtime_data.cloud.mqtt is None
    assert (client.requests, client.schedule_list_requests) == (0, requests)
