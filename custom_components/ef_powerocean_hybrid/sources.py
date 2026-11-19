"""Where a joined value comes from: Modbus, then fresh MQTT, then fresh HTTP.

A value both sources have is one entity. It shows Modbus while Modbus has a
trusted, current reading; otherwise the cloud's MQTT live value if it is fresh;
otherwise the cloud's HTTP value if its report is fresh; otherwise nothing, so
the entity is unavailable rather than showing an old number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from .catalogue import CloudSource, cloud_value

if TYPE_CHECKING:
    from .cloud.coordinator import CloudCoordinator
    from .coordinator import EcoflowCoordinator

type Source = Literal["modbus", "mqtt", "cloud"]


@dataclass(frozen=True)
class Reading:
    """A value and when its source measured it."""

    value: float
    at: datetime


def _fresh(reading: Reading | None, now: datetime, limit: timedelta) -> bool:
    # The cloud's clock may run ahead of ours: a time in the future counts only
    # as far ahead as a past one may lie behind.
    return reading is not None and abs(now - reading.at) <= limit


def pick(
    modbus: Reading | None,
    mqtt: Reading | None,
    http: Reading | None,
    *,
    now: datetime,
    mqtt_limit: timedelta,
    http_limit: timedelta,
) -> tuple[float, Source] | None:
    """Return the value to show and its source, or None if no source is fresh.

    *modbus* is already checked by its reader: online, present, trusted, current.
    """
    if modbus is not None:
        return modbus.value, "modbus"
    if mqtt is not None and _fresh(mqtt, now, mqtt_limit):
        return mqtt.value, "mqtt"
    if http is not None and _fresh(http, now, http_limit):
        return http.value, "cloud"
    return None


# A Modbus reading older than this many polls is no longer current.
MODBUS_POLLS: int = 3


def modbus_reading(
    coordinator: EcoflowCoordinator, key: str, now: datetime
) -> Reading | None:
    """Return Modbus's reading of *key* if connected, online, present, trusted
    and current."""
    if not coordinator.last_update_success or not coordinator.connected:
        return None
    value = (coordinator.data or {}).get(key)
    if value is None or key in coordinator.control.reading_checks.distrusted:
        return None
    at = coordinator.last_good_poll
    if at is None or now - at > timedelta(
        seconds=MODBUS_POLLS * coordinator.scan_interval
    ):
        return None
    return Reading(float(value), at)


def http_reading(
    cloud: CloudCoordinator, serial: str, source: CloudSource
) -> Reading | None:
    """Return the cloud's HTTP value of *source*, timed by Home Assistant's clock.

    The time is when Home Assistant last saw the report's time change, so the
    cloud's own clock, fast or slow, never makes a value look fresh.
    """
    seen = cloud.report_seen.get(source.time_key or "")
    value = cloud_value(cloud.data or {}, serial, source)
    if seen is None or not isinstance(value, int | float):
        return None
    return Reading(float(value), seen)


def mqtt_reading(cloud: CloudCoordinator, key: str) -> Reading | None:
    """Return the cloud's MQTT live reading of *key*, if any."""
    return cloud.live.get(key)
