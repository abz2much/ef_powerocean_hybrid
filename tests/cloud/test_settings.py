"""The settings over MQTT, as the cloud integration runs them (C2).

LED brightness, backup reserve, surplus solar energy delivery and battery save
mode: read from the HTTP response and the MQTT reports, written over MQTT,
each command waiting for its acknowledgement. Ported from powerocean_single_phase
tests/integration/test_mqtt.py (v1.0.1, 45287c9); the model-86 fixture holds
LED brightness 100, backup reserve 5, charge limit 100 and devSoc 100.

The LED brightness is also a Modbus setting (register 40541), so it is one
entity: the Modbus integration's number while Modbus is online, the cloud
integration's otherwise. Its cloud tests run on a cloud-only entry.

The backup reserve is also a Modbus reading (register 40536, the Modbus
integration's App Backup Reserve sensor), so it is one number: read from Modbus
while it is online, else from the cloud, and always written over MQTT, the only
side that writes it. Its cloud tests run on a cloud-only entry.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import timedelta
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud.api import EcoflowConnectionError
from custom_components.ef_powerocean_hybrid.coordinator import EcoflowCoordinator
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response
from tests.cloud.test_mqtt_link import FakeMqtt
from tests.fake_inverter import HOST, FakeInverter

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# The Modbus integration's LED number, which the cloud's joins
LED_KEY = "device_led_brightness_control"
LED_NUMBER = f"{SERIAL}_{LED_KEY}"
CLOUD_LED_NUMBER = f"{CLOUD_SN}_{LED_KEY}"
LED_REGISTER = 40541
BACKUP_NUMBER = f"{SERIAL}_backup_reserve"
CLOUD_BACKUP_NUMBER = f"{CLOUD_SN}_backup_reserve"
RESERVE_REGISTER = 40536
# The Modbus integration's read-only twin of the backup reserve
APP_RESERVE_SENSOR = f"{SERIAL}_min_soc_limit"
SOLAR_NUMBER = f"{SERIAL}_solar_surplus"
SAVE_SWITCH = f"{SERIAL}_battery_save_mode"
# Only the cloud has these
SETTINGS = (
    ("number", BACKUP_NUMBER),
    ("number", SOLAR_NUMBER),
    ("switch", SAVE_SWITCH),
)
# The cloud's values the settings also change
LED_VALUE = f"{CLOUD_SN}_JTS1_EMS_CHANGE_REPORT_emsCtrlLedBright"
DISCHARGE_LIMIT = f"{CLOUD_SN}_sysBatDsgDownLimit"
TIMEOUT = "custom_components.ef_powerocean_hybrid.cloud.coordinator.COMMAND_TIMEOUT"


class SettingsMqtt(FakeMqtt):
    """Records the commands and acknowledges them, as the system does."""

    # False: the system does not answer
    acknowledge: ClassVar[bool] = True

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.led_commands: list[int] = []
        self.soc_limit_commands: list[tuple[int, int, int]] = []
        self.battery_save_commands: list[tuple[int, int]] = []

    def set_led_brightness(self, percent: int, seq: int) -> None:
        self.led_commands.append(percent)
        if SettingsMqtt.acknowledge:
            self.callbacks.on_ack(seq)

    def set_soc_limits(
        self, charge_limit: int, backup_reserve: int, dev_soc: int, seq: int
    ) -> None:
        self.soc_limit_commands.append((charge_limit, backup_reserve, dev_soc))
        if SettingsMqtt.acknowledge:
            self.callbacks.on_ack(seq)

    def set_battery_save_mode(self, enabled: int, zone: int, seq: int) -> None:
        self.battery_save_commands.append((enabled, zone))
        if SettingsMqtt.acknowledge:
            self.callbacks.on_ack(seq)


@pytest.fixture(autouse=True)
def fake_mqtt() -> Iterator[None]:
    FakeMqtt.instances.clear()
    SettingsMqtt.acknowledge = True
    with patch(
        "custom_components.ef_powerocean_hybrid.cloud.coordinator.PowerOceanMqtt",
        SettingsMqtt,
    ):
        yield


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    set_up: SetUp,
    *,
    mqtt: bool | None = True,
    api: FakeApi | None = None,
) -> FakeApi:
    if mqtt is not None:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, const.CONF_MQTT: mqtt}
        )
    api = api or FakeApi(fetch=[model86_response()])
    await set_up(entry, api)
    return api


def _client() -> SettingsMqtt:
    (client,) = FakeMqtt.instances
    assert isinstance(client, SettingsMqtt)
    return client


def _entity_id(hass: HomeAssistant, unique_id: str, domain: str = "number") -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, const.DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


def _state(hass: HomeAssistant, unique_id: str, domain: str = "number") -> str:
    state = hass.states.get(_entity_id(hass, unique_id, domain))
    assert state
    return state.state


def _source(hass: HomeAssistant, unique_id: str) -> Any:
    state = hass.states.get(_entity_id(hass, unique_id))
    assert state
    return state.attributes.get("source")


async def _set(hass: HomeAssistant, unique_id: str, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _entity_id(hass, unique_id), "value": value},
        blocking=True,
    )


async def _switch_save(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch",
        service,
        {"entity_id": _entity_id(hass, SAVE_SWITCH, "switch")},
        blocking=True,
    )


async def _report(hass: HomeAssistant, values: dict[str, int]) -> None:
    """The system reports changed settings (paho's thread)."""
    await hass.async_add_executor_job(_client().callbacks.on_settings, values)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The next HTTP request."""
    await entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()


async def _fail_modbus(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch.object(
        EcoflowCoordinator, "async_get_raw_data", side_effect=UpdateFailed("down")
    ):
        await entry.runtime_data.modbus.async_refresh()
    await hass.async_block_till_done()


def _with(report: str, name: str, value: int) -> dict[str, Any]:
    response = model86_response()
    response["data"]["quota"][report][name] = value
    return response


def _registered(hass: HomeAssistant, domain: str, unique_id: str) -> bool:
    return (
        er.async_get(hass).async_get_entity_id(domain, const.DOMAIN, unique_id)
        is not None
    )


def _led_numbers(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    return [
        registry_entry.unique_id
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if registry_entry.domain == "number" and "led" in registry_entry.unique_id
    ]


# Which entries have them


@pytest.mark.parametrize("mqtt", (None, False))
async def test_settings_only_with_mqtt(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    mqtt: bool | None,
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=mqtt)

    assert not any(_registered(hass, *setting) for setting in SETTINGS)
    # Modbus has the LED brightness
    assert _registered(hass, "number", LED_NUMBER)


async def test_settings_removed_when_switched_off(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)
    assert all(_registered(hass, *setting) for setting in SETTINGS)

    hass.config_entries.async_update_entry(
        hybrid_entry, options={**hybrid_entry.options, const.CONF_MQTT: False}
    )
    assert await hass.config_entries.async_reload(hybrid_entry.entry_id)
    await hass.async_block_till_done()

    assert not any(_registered(hass, *setting) for setting in SETTINGS)
    assert _registered(hass, "number", LED_NUMBER)


async def test_a_cloud_only_entry_has_the_settings(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)

    assert _state(hass, CLOUD_LED_NUMBER) == "100"
    assert _state(hass, f"{CLOUD_SN}_backup_reserve") == "5"


async def test_a_cloud_only_entry_has_no_led_number_without_mqtt(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    # One left from when MQTT was on
    er.async_get(hass).async_get_or_create(
        "number", const.DOMAIN, CLOUD_LED_NUMBER, config_entry=cloud_entry
    )

    await _setup(hass, cloud_entry, set_up_hybrid, mqtt=False)

    assert not _registered(hass, "number", CLOUD_LED_NUMBER)


@pytest.mark.parametrize("mqtt", (False, True))
async def test_one_led_number_on_a_hybrid_entry(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    mqtt: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The LED brightness is one value on both sides: one entity, Modbus's."""
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=mqtt)

    assert _led_numbers(hass, hybrid_entry) == [LED_NUMBER]
    # Not a second entity under the same unique ID, which Home Assistant drops
    assert "does not generate unique IDs" not in caplog.text


async def test_a_modbus_only_entry_keeps_its_led_number(
    hass: HomeAssistant, inverter: FakeInverter
) -> None:
    inverter.serve_serial(SERIAL, const.SERIAL_NUMBER.address)
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={const.CONF_HOST: HOST, const.CONF_PORT: inverter.port},
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _led_numbers(hass, entry) == [LED_NUMBER]
    assert not any(_registered(hass, *setting) for setting in SETTINGS)

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_settings_available_once_mqtt_starts_late(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    """MQTT starts with the cloud's first answer; the settings come with it."""
    api = FakeApi(login=EcoflowConnectionError("offline"), fetch=[model86_response()])
    await _setup(hass, hybrid_entry, set_up_hybrid, api=api)
    assert _state(hass, SOLAR_NUMBER) == STATE_UNAVAILABLE

    api.recover()
    await hybrid_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _client().started
    assert _state(hass, SOLAR_NUMBER) == "100"


async def test_settings_unavailable_without_connection(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[model86_response()])
    api.mqtt_credentials = EcoflowConnectionError("offline")

    await _setup(hass, hybrid_entry, set_up_hybrid, api=api)

    assert not FakeMqtt.instances
    for domain, unique_id in SETTINGS:
        if unique_id != BACKUP_NUMBER:
            assert _state(hass, unique_id, domain) == STATE_UNAVAILABLE
    # The backup reserve still shows the Modbus reading
    assert _source(hass, BACKUP_NUMBER) == "modbus"


# LED brightness: Modbus while it is online, the cloud otherwise


async def test_led_brightness_entity(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    inverter.registers[LED_REGISTER] = 30
    await _setup(hass, hybrid_entry, set_up_hybrid)

    entity = er.async_get(hass).async_get(_entity_id(hass, LED_NUMBER))
    assert entity
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.translation_key == LED_KEY
    device = dr.async_get(hass).async_get_device_by_identifier(
        (const.DOMAIN, SERIAL), hybrid_entry.entry_id
    )
    assert device
    assert entity.device_id == device.id
    state = hass.states.get(entity.entity_id)
    assert state
    # Modbus comes first
    assert state.state == "30"
    assert state.attributes["source"] == "modbus"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 100
    assert state.attributes["step"] == 10
    assert state.attributes["unit_of_measurement"] == "%"


async def test_led_brightness_set_over_modbus_while_it_is_online(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    inverter.registers[LED_REGISTER] = 30
    await _setup(hass, hybrid_entry, set_up_hybrid)

    await _set(hass, LED_NUMBER, 60)

    assert inverter.registers[LED_REGISTER] == 60
    assert _client().led_commands == []
    assert _state(hass, LED_NUMBER) == "60"


async def test_led_brightness_from_the_cloud_when_modbus_fails(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    inverter.registers[LED_REGISTER] = 30
    await _setup(hass, hybrid_entry, set_up_hybrid)

    await _fail_modbus(hass, hybrid_entry)
    assert _state(hass, LED_NUMBER) == "100"
    assert _source(hass, LED_NUMBER) == "cloud"

    await _set(hass, LED_NUMBER, 40)

    assert _client().led_commands == [40]
    assert inverter.registers[LED_REGISTER] == 30
    assert _state(hass, LED_NUMBER) == "40"


async def test_led_brightness_unavailable_without_modbus_or_mqtt(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=False)

    await _fail_modbus(hass, hybrid_entry)

    assert _state(hass, LED_NUMBER) == STATE_UNAVAILABLE


async def test_set_led_brightness(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)

    await _set(hass, CLOUD_LED_NUMBER, 40)

    assert _client().led_commands == [40]
    # Shown right after the acknowledgement, also in the cloud's value
    assert _state(hass, CLOUD_LED_NUMBER) == "40"
    assert _source(hass, CLOUD_LED_NUMBER) == "cloud"
    assert cloud_entry.runtime_data.cloud.data[LED_VALUE] == 40


async def test_led_brightness_reported_by_the_system(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)

    # Changed in the app: the system reports it (EMS change report)
    await _report(hass, {"emsCtrlLedBright": 60})

    assert _state(hass, CLOUD_LED_NUMBER) == "60"


async def test_led_brightness_after_unload_is_ignored(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)
    cloud = cloud_entry.runtime_data.cloud
    client = _client()
    assert await hass.config_entries.async_unload(cloud_entry.entry_id)

    await hass.async_add_executor_job(
        client.callbacks.on_settings, {"emsCtrlLedBright": 60}
    )
    await hass.async_block_till_done()

    assert cloud.data[LED_VALUE] == 100


async def test_set_led_brightness_without_acknowledgement(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)
    SettingsMqtt.acknowledge = False

    with patch(TIMEOUT, 0.01), pytest.raises(HomeAssistantError) as error:
        await _set(hass, CLOUD_LED_NUMBER, 40)

    assert error.value.translation_key == "not_acknowledged"
    assert _state(hass, CLOUD_LED_NUMBER) == "100"


# Backup reserve


async def test_backup_reserve_entity(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    inverter.registers[RESERVE_REGISTER] = 12
    await _setup(hass, hybrid_entry, set_up_hybrid)

    entity = er.async_get(hass).async_get(_entity_id(hass, BACKUP_NUMBER))
    assert entity
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.translation_key == "backup_reserve"
    state = hass.states.get(entity.entity_id)
    assert state
    # Modbus comes first
    assert state.state == "12"
    assert state.attributes["source"] == "modbus"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 100
    assert state.attributes["step"] == 1
    assert state.attributes["unit_of_measurement"] == "%"


async def test_set_backup_reserve(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)

    await _set(hass, CLOUD_BACKUP_NUMBER, 16)

    # Charge limit and devSoc are sent unchanged, as the app does
    assert _client().soc_limit_commands == [(100, 16, 100)]
    assert _state(hass, CLOUD_BACKUP_NUMBER) == "16"
    assert cloud_entry.runtime_data.cloud.data[DISCHARGE_LIMIT] == 16


async def test_backup_reserve_written_over_mqtt_while_modbus_is_online(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    """Only the cloud integration writes it; the Modbus one only reads it."""
    inverter.registers[RESERVE_REGISTER] = 12
    await _setup(hass, hybrid_entry, set_up_hybrid)

    await _set(hass, BACKUP_NUMBER, 16)

    assert _client().soc_limit_commands == [(100, 16, 100)]
    assert inverter.registers[RESERVE_REGISTER] == 12


async def test_backup_reserve_from_the_cloud_when_modbus_fails(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    inverter: FakeInverter,
) -> None:
    inverter.registers[RESERVE_REGISTER] = 12
    await _setup(hass, hybrid_entry, set_up_hybrid)

    await _fail_modbus(hass, hybrid_entry)

    assert _state(hass, BACKUP_NUMBER) == "5"
    assert _source(hass, BACKUP_NUMBER) == "cloud"


async def test_backup_reserve_unavailable_without_modbus_or_mqtt(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = FakeApi(fetch=[model86_response()])
    api.mqtt_credentials = EcoflowConnectionError("offline")
    await _setup(hass, hybrid_entry, set_up_hybrid, api=api)

    await _fail_modbus(hass, hybrid_entry)

    assert _state(hass, BACKUP_NUMBER) == STATE_UNAVAILABLE


@pytest.mark.parametrize("mqtt", (False, True))
async def test_one_backup_reserve_on_a_hybrid_entry(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    mqtt: bool,
) -> None:
    """With MQTT the number, without it the Modbus sensor; never both."""
    # The Modbus sensor, as an entry that had it before MQTT was turned on
    er.async_get(hass).async_get_or_create(
        "sensor", const.DOMAIN, APP_RESERVE_SENSOR, config_entry=hybrid_entry
    )

    await _setup(hass, hybrid_entry, set_up_hybrid, mqtt=mqtt)

    assert _registered(hass, "number", BACKUP_NUMBER) is mqtt
    assert _registered(hass, "sensor", APP_RESERVE_SENSOR) is not mqtt


async def test_backup_reserve_sends_the_reported_charge_limit(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)

    # Changed in the app: the system reports both values
    await _report(hass, {"sysBatChgUpLimit": 90, "sysBatDsgDownLimit": 20})
    assert _state(hass, CLOUD_BACKUP_NUMBER) == "20"

    await _set(hass, CLOUD_BACKUP_NUMBER, 10)

    assert _client().soc_limit_commands == [(90, 10, 100)]


async def test_backup_reserve_not_sent_without_dev_soc(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    response = copy.deepcopy(model86_response())
    del response["data"]["quota"]["JTS1_EMS_PARAM_CHANGE_REPORT"]["devSoc"]
    await _setup(hass, cloud_entry, set_up_hybrid, api=FakeApi(fetch=[response]))

    assert _state(hass, CLOUD_BACKUP_NUMBER) == STATE_UNAVAILABLE
    with pytest.raises(HomeAssistantError) as error:
        await cloud_entry.runtime_data.cloud.async_set_backup_reserve(16)

    assert error.value.translation_key == "soc_limits_unknown"
    assert _client().soc_limit_commands == []


async def test_set_backup_reserve_without_acknowledgement(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)
    SettingsMqtt.acknowledge = False

    with patch(TIMEOUT, 0.01), pytest.raises(HomeAssistantError) as error:
        await _set(hass, CLOUD_BACKUP_NUMBER, 16)

    assert error.value.translation_key == "not_acknowledged"
    assert _state(hass, CLOUD_BACKUP_NUMBER) == "5"


# Surplus solar energy delivery


async def test_solar_surplus_entity(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)

    entity = er.async_get(hass).async_get(_entity_id(hass, SOLAR_NUMBER))
    assert entity
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.translation_key == "solar_surplus"
    state = hass.states.get(entity.entity_id)
    assert state
    # devSoc from the EMS parameter change report
    assert state.state == "100"
    assert state.attributes["step"] == 1
    assert state.attributes["unit_of_measurement"] == "%"


async def test_set_solar_surplus(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)

    await _set(hass, SOLAR_NUMBER, 77)

    # Charge limit and backup reserve are sent unchanged, as the app does
    assert _client().soc_limit_commands == [(100, 5, 77)]
    assert _state(hass, SOLAR_NUMBER) == "77"
    # The backup reserve now sends the new value along
    await _set(hass, BACKUP_NUMBER, 16)
    assert _client().soc_limit_commands[-1] == (100, 16, 77)


async def test_solar_surplus_reported_by_the_system(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)

    # Changed in the app: the system reports it (EMS parameter change report)
    await _report(hass, {"devSoc": 77})

    assert _state(hass, SOLAR_NUMBER) == "77"


# Battery save mode


async def test_battery_save_mode_unavailable_until_reported(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)

    entity = er.async_get(hass).async_get(_entity_id(hass, SAVE_SWITCH, "switch"))
    assert entity
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.translation_key == "battery_save_mode"
    # Not in the HTTP response: only known from the MQTT report
    assert _state(hass, SAVE_SWITCH, "switch") == STATE_UNAVAILABLE
    with pytest.raises(HomeAssistantError) as error:
        await hybrid_entry.runtime_data.cloud.async_set_battery_save_mode(enabled=True)
    assert error.value.translation_key == "sys_zone_unknown"

    # Full state after connecting: parameter change report
    await _report(hass, {"batterySaveMode": 1, "sysZone": 4})
    assert _state(hass, SAVE_SWITCH, "switch") == "on"

    # Switched off in the app
    await _report(hass, {"batterySaveMode": 0})
    assert _state(hass, SAVE_SWITCH, "switch") == "off"


async def test_switch_battery_save_mode(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)
    await _report(hass, {"batterySaveMode": 1, "sysZone": 4})

    await _switch_save(hass, "turn_off")
    assert _state(hass, SAVE_SWITCH, "switch") == "off"
    await _switch_save(hass, "turn_on")
    assert _state(hass, SAVE_SWITCH, "switch") == "on"

    # sysZone is sent unchanged, as the app does
    assert _client().battery_save_commands == [(0, 4), (1, 4)]


async def test_switch_battery_save_mode_without_acknowledgement(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)
    await _report(hass, {"batterySaveMode": 1, "sysZone": 4})
    SettingsMqtt.acknowledge = False

    with patch(TIMEOUT, 0.01), pytest.raises(HomeAssistantError) as error:
        await _switch_save(hass, "turn_off")

    assert error.value.translation_key == "not_acknowledged"
    assert _state(hass, SAVE_SWITCH, "switch") == "on"


# Confirmed settings and the HTTP response: the cloud shows a change only
# 20-60 s later, so a confirmed value wins for up to 2 minutes.


async def test_set_led_brightness_kept_while_the_cloud_is_behind(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)

    await _set(hass, CLOUD_LED_NUMBER, 40)
    # The cloud still shows the old value
    await _poll(hass, cloud_entry)

    assert _state(hass, CLOUD_LED_NUMBER) == "40"


async def test_set_led_brightness_hold_ends_when_the_cloud_shows_it(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    api = await _setup(hass, cloud_entry, set_up_hybrid)
    await _set(hass, CLOUD_LED_NUMBER, 40)

    api.respond(_with("JTS1_EMS_CHANGE_REPORT", "emsCtrlLedBright", 40))
    await _poll(hass, cloud_entry)
    # Later changed in the app, only the cloud shows it
    api.respond(_with("JTS1_EMS_CHANGE_REPORT", "emsCtrlLedBright", 70))
    await _poll(hass, cloud_entry)

    assert _state(hass, CLOUD_LED_NUMBER) == "70"


async def test_set_led_brightness_hold_ends_after_two_minutes(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, cloud_entry, set_up_hybrid)
    await _set(hass, CLOUD_LED_NUMBER, 40)

    freezer.tick(timedelta(minutes=2, seconds=10))
    await _poll(hass, cloud_entry)

    # The cloud never showed it: its value counts again
    assert _state(hass, CLOUD_LED_NUMBER) == "100"


async def test_backup_reserve_kept_for_the_next_command(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)

    await _set(hass, BACKUP_NUMBER, 16)
    # The cloud still shows the old backup reserve 5
    await _poll(hass, hybrid_entry)
    await _set(hass, SOLAR_NUMBER, 80)

    # Not (100, 5, 80): that would undo the backup reserve on the system
    assert _client().soc_limit_commands == [(100, 16, 100), (100, 16, 80)]
    assert hybrid_entry.runtime_data.cloud.data[DISCHARGE_LIMIT] == 16


async def test_reported_setting_kept_while_the_cloud_is_behind(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await _setup(hass, hybrid_entry, set_up_hybrid)

    # Changed in the app: the system reports it before the cloud shows it
    await _report(hass, {"devSoc": 77})
    await _poll(hass, hybrid_entry)

    assert _state(hass, SOLAR_NUMBER) == "77"
