"""B8 style for the MQTT entities: exactly what powerocean_single_phase adds.

With the option MQTT: the settings (backup reserve, surplus solar energy
delivery, battery save mode), a switch per schedule and the Active schedule
sensor, and the LED brightness once. Without it: none of them; the LED
brightness only on an entry with Modbus.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL
from tests.cloud.schedule_fakes import schedule_system, setup

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

LED = ("number", "device_led_brightness_control")
MQTT_ONLY = {
    ("number", "backup_reserve"),
    ("number", "solar_surplus"),
    ("switch", "battery_save_mode"),
    ("switch", "schedule_10"),
    ("switch", "schedule_11"),
    ("switch", "schedule_12"),
    ("sensor", "active_schedule"),
}


def _entities(
    hass: HomeAssistant, entry: MockConfigEntry, serial: str
) -> set[tuple[str, str]]:
    """Return (domain, key) of the entry's number, switch and sensor entities."""
    return {
        (e.domain, e.unique_id.removeprefix(f"{serial}_"))
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain in ("number", "switch", "sensor")
    }


@pytest.mark.parametrize("mqtt", (True, False))
async def test_a_hybrid_entry_has_the_mqtt_entities_once(
    hass: HomeAssistant,
    hybrid_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    mqtt: bool,
) -> None:
    with schedule_system():
        await setup(hass, hybrid_entry, set_up_hybrid, mqtt=mqtt)
        entities = _entities(hass, hybrid_entry, SERIAL)

    # The LED brightness is one joined number, with or without MQTT.
    assert LED in entities
    assert (entities & MQTT_ONLY) == (MQTT_ONLY if mqtt else set())


@pytest.mark.parametrize("mqtt", (True, False))
async def test_a_cloud_only_entry_has_the_mqtt_entities_only_with_mqtt(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    set_up_hybrid: SetUp,
    mqtt: bool,
) -> None:
    with schedule_system():
        await setup(hass, cloud_entry, set_up_hybrid, mqtt=mqtt)
        entities = _entities(hass, cloud_entry, CLOUD_SN)

    expected = MQTT_ONLY | {LED}
    assert (entities & expected) == (expected if mqtt else set())


async def test_a_modbus_only_entry_has_no_mqtt_entities(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        hybrid_entry,
        data={
            k: v
            for k, v in hybrid_entry.data.items()
            if k in (const.CONF_HOST, const.CONF_PORT)
        },
        options={**hybrid_entry.options, const.CONF_MQTT: True},
    )
    assert await hass.config_entries.async_setup(hybrid_entry.entry_id)
    await hass.async_block_till_done()

    entities = _entities(hass, hybrid_entry, SERIAL)

    assert LED in entities
    assert not entities & MQTT_ONLY
