"""A cloud value that is not what its sensor expects shows as unavailable."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.catalogue import (
    CATALOGUE_86,
    CatalogueEntry,
)
from custom_components.ef_powerocean_hybrid.cloud_sensor import CloudSensor
from tests.cloud.fakes import CLOUD_SN


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=const.DOMAIN,
        unique_id=CLOUD_SN,
        data={
            CONF_EMAIL: "owner@example.com",
            CONF_PASSWORD: "secret",
            const.CONF_CLOUD_SERIAL: CLOUD_SN,
        },
    )


def _sensor(key: str, values: dict[str, Any]) -> CloudSensor:
    definition: CatalogueEntry = next(e for e in CATALOGUE_86 if e.key == key)
    coordinator = SimpleNamespace(
        data=values, last_update_success=True, async_add_listener=lambda *_: None
    )
    return CloudSensor(cast(Any, coordinator), _entry(), definition)


def test_text_on_a_numeric_sensor_is_unavailable() -> None:
    sensor = _sensor(
        "solar_total_cloud", {f"{CLOUD_SN}_totalElectricityGeneration": "FALSE"}
    )

    assert sensor.available is False


def test_text_on_a_timestamp_sensor_is_unavailable() -> None:
    sensor = _sensor(
        "heartbeat_update_time",
        {f"{CLOUD_SN}_JTS1_EMS_HEARTBEAT_heartbeatUpdateTime": "FALSE"},
    )

    assert sensor.available is False


def test_a_number_sent_as_text_still_counts() -> None:
    sensor = _sensor(
        "solar_total_cloud", {f"{CLOUD_SN}_totalElectricityGeneration": "7498.12"}
    )

    assert sensor.available is True
    assert sensor.native_value == 7498.12


def test_a_text_sensor_keeps_its_text() -> None:
    sensor = _sensor(
        "pcs_run_state", {f"{CLOUD_SN}_JTS1_EMS_CHANGE_REPORT_pcsRunSta": "RUNSTA_RUN"}
    )

    assert sensor.available is True
    assert sensor.native_value == "RUNSTA_RUN"
