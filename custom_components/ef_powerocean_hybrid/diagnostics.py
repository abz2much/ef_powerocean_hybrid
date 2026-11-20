"""Diagnostics: the Modbus side, and the cloud part of powerocean_single_phase."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .cloud.anonymize import Anonymizer
from .cloud.coordinator import CloudCoordinator
from .const import (
    CONF_CLOUD_SERIAL,
    CONF_HOST,
    CONTROL_FEATURES,
    DOMAIN,
    SERIAL_NUMBER,
)
from .coordinator import EcoflowConfigEntry
from .modbus import is_shared

TO_REDACT = (
    CONF_HOST,
    "title",
    "unique_id",
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_CLOUD_SERIAL,
)

# Raw readings worth having in a model report, to compare how each model fills them.
PROTOCOL_REPORT_KEYS = (
    "system_modes_hex",
    "system_state_2_hex",
    "system_alerts",
    "active_control_mode",
    "device_modbus_control",
    "bms_connected",
    "grid_feed_mode",
    "feed_in_power_max_setting",
    "feed_in_power_max_effective",
    "feed_in_power_max_percent",
    "limit_inv_power",
    "limit_inv_max",
    "inverter_rated_power",
)


def redact_raw_registers(
    raw_registers: dict[int, list[int]],
) -> dict[int, list[int | None]]:
    """Return the raw reads with any word of the serial number replaced by None.

    The polls do not read it today; this keeps it out should a read ever cover it.
    """
    serial = range(SERIAL_NUMBER.address, SERIAL_NUMBER.end)
    return {
        start: [
            None if start + offset in serial else word
            for offset, word in enumerate(words)
        ]
        for start, words in raw_registers.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001 - required by the diagnostics API
    entry: EcoflowConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    cloud = entry.runtime_data.cloud
    cloud_part = None if cloud is None else _cloud_diagnostics(cloud)
    coordinator = entry.runtime_data.modbus
    if coordinator is None:
        return {
            **async_redact_data(
                {"entry": entry.as_dict(), "domain": DOMAIN, "modbus": None}, TO_REDACT
            ),
            "cloud": cloud_part,
        }

    identity = asdict(coordinator.identity)
    if identity["serial_number"] not in (None, "unknown"):
        identity["serial_number"] = identity["serial_number"][:4]

    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "domain": DOMAIN,
            "inverter_model": coordinator.inverter_model,
            **identity,
            "pymodbus": coordinator.get_pymodbus_version(),
            "modbus_connection": "shared" if is_shared() else "own",
            "heartbeat_supported": coordinator.control.heartbeat_supported,
            "modbus_control_enabled": coordinator.control.enabled,
            "last_heartbeat_time": coordinator.control.last_heartbeat_time,
            "in_control": coordinator.control.in_control,
            "selected_feature": str(coordinator.control.selected_feature),
            "command_expires_at": coordinator.control.expires_at,
            "control_status": str(coordinator.control.status),
            "control_guard": (
                str(guard) if (guard := coordinator.control.active_guard) else None
            ),
            "feature_power": {
                str(feature): coordinator.control.feature_power(feature)
                for feature in CONTROL_FEATURES
            },
            "charge_limit_soc": coordinator.control.charge_limit_soc,
            "battery_reserve_soc": coordinator.control.battery_reserve_soc,
            "control_method": str(coordinator.control.method),
            "control_power": coordinator.control.power,
            "control_command": f"0x{coordinator.control.command:08X}",
            "unsupported_registers": sorted(coordinator.unsupported_registers),
            # The words the last poll read, undecoded, which the test suite's fake
            # inverter can serve again. Never the serial number.
            "raw_registers": redact_raw_registers(coordinator.raw_registers),
            # Every energy counter as published and as the device reports it.
            "energy": coordinator.energy_report,
            # The last control test run here, the report to attach to an issue.
            "control_test": {
                "state": str(coordinator.control_test.state),
                "last_report": coordinator.control_test.last_report,
            },
            # The readings each time a reading check started failing, kept across
            # restarts, so a report filed later still shows what the inverter sent.
            "reading_check_snapshots": [
                {
                    **snapshot,
                    "raw_registers": redact_raw_registers(snapshot["raw_registers"]),
                    "raw_registers_before": (
                        redact_raw_registers(before)
                        if (before := snapshot["raw_registers_before"]) is not None
                        else None
                    ),
                }
                for snapshot in coordinator.reading_check_snapshots
            ],
            "protocol_report": {
                key: (coordinator.data or {}).get(key) for key in PROTOCOL_REPORT_KEYS
            },
        },
        TO_REDACT,
    ) | {"cloud": cloud_part}


def _cloud_diagnostics(cloud: CloudCoordinator) -> dict[str, Any]:
    """Return the cloud part, as powerocean_single_phase (v1.0.1) gives it.

    Not passed through async_redact_data: the serials in the response become
    placeholders instead, so the response can be used as a test fixture.
    """
    return {
        "coordinator": {
            "last_update_success": cloud.last_update_success,
            "last_exception": (
                repr(cloud.last_exception) if cloud.last_exception else None
            ),
            "update_interval": (
                cloud.update_interval.total_seconds() if cloud.update_interval else None
            ),
            "variant": cloud.api.variant,
            "mqtt": cloud.mqtt is not None,
        },
        # Firmware of the modules from MQTT, by address (without serials). The
        # EMS firmware is shown here only.
        "modules": {
            address: {
                "type": module.module_type,
                "firmware": module.firmware,
                "loader": module.loader,
            }
            for address, module in sorted(cloud.modules.items())
        },
        "api_response": Anonymizer(cloud.api.sn).anonymize(cloud.last_response),
    }
