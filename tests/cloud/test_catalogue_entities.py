"""B8: the sensors an entry gets match the catalogue, value by value."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.catalogue import CATALOGUE_86
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

PACKS = ("SN_BP01", "SN_BP02")
JOINED = {e.key for e in CATALOGUE_86 if e.kind == "joined"}
CLOUD_INVERTER = {e.key for e in CATALOGUE_86 if e.kind == "cloud" and not e.per_pack}
CLOUD_PACK = {e.key for e in CATALOGUE_86 if e.kind == "cloud" and e.per_pack}
CATALOGUE_KEYS = {e.key for e in CATALOGUE_86}
CLOUD_COUNTERS = {"bat_charged_total_cloud", "bat_discharged_total_cloud"}
# Modbus sensors outside the catalogue's values: the inverter's own daily and
# lifetime registers as reported, and the control and diagnostic sensors.
MODBUS_EXTRAS = {
    *(f"{d.key}" for d in const.DAILY_ENERGY_SENSORS_DEVICE_RAW),
    *(f"{d.key}" for d in const.TOTAL_ENERGY_SENSORS_DEVICE_RAW),
    "control_status",
    "battery_reserve_effective",
    "control_test",
}


def _sensor_keys(hass: HomeAssistant, entry: MockConfigEntry, serial: str) -> set[str]:
    return {
        e.unique_id.removeprefix(f"{serial}_")
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == "sensor" and e.unique_id.startswith(f"{serial}_")
    }


def _pack_keys(hass: HomeAssistant, entry: MockConfigEntry, pack: str) -> set[str]:
    return _sensor_keys(hass, entry, pack)


async def test_a_hybrid_entry_has_every_value_once(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    keys = _sensor_keys(hass, hybrid_entry, SERIAL)

    assert keys >= JOINED | CLOUD_INVERTER
    assert keys <= CATALOGUE_KEYS | MODBUS_EXTRAS
    for pack in PACKS:
        assert _pack_keys(hass, hybrid_entry, pack) == CLOUD_PACK


@pytest.fixture
async def cloud_entry(hass: HomeAssistant) -> AsyncIterator[MockConfigEntry]:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=CLOUD_SN,
        minor_version=2,
        data={
            CONF_EMAIL: "owner@example.com",
            CONF_PASSWORD: "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        },
        options={const.CONF_INVERTER_MODEL: "powerocean_single_phase"},
    )
    entry.add_to_hass(hass)
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_cloud_only_entry_has_exactly_the_cloud_values(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(cloud_entry, FakeApi(fetch=[model86_response()]))

    assert (
        _sensor_keys(hass, cloud_entry, CLOUD_SN)
        == JOINED | CLOUD_INVERTER | CLOUD_COUNTERS
    )
    for pack in PACKS:
        assert _pack_keys(hass, cloud_entry, pack) == CLOUD_PACK


async def test_a_modbus_only_single_phase_has_no_cloud_values(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        hybrid_entry,
        data={
            k: v
            for k, v in hybrid_entry.data.items()
            if k in (const.CONF_HOST, const.CONF_PORT)
        },
    )
    assert await hass.config_entries.async_setup(hybrid_entry.entry_id)
    await hass.async_block_till_done()

    keys = _sensor_keys(hass, hybrid_entry, SERIAL)

    assert not keys & (CLOUD_INVERTER | CLOUD_COUNTERS)
    assert keys >= JOINED
    assert keys <= CATALOGUE_KEYS | MODBUS_EXTRAS
