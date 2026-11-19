"""Which source a joined value comes from: Modbus, then fresh MQTT, then fresh HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.ef_powerocean_hybrid.sources import Reading, pick

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
MQTT_LIMIT = timedelta(seconds=30)
HTTP_LIMIT = timedelta(seconds=60)


def _at(seconds_ago: float, value: float = 1.0) -> Reading:
    return Reading(value, NOW - timedelta(seconds=seconds_ago))


def _pick(modbus: Reading | None, mqtt: Reading | None, http: Reading | None):
    return pick(
        modbus, mqtt, http, now=NOW, mqtt_limit=MQTT_LIMIT, http_limit=HTTP_LIMIT
    )


def test_modbus_wins() -> None:
    assert _pick(_at(0, 1), _at(0, 2), _at(0, 3)) == (1, "modbus")


def test_mqtt_when_there_is_no_modbus() -> None:
    assert _pick(None, _at(5, 2), _at(0, 3)) == (2, "mqtt")


def test_http_when_there_is_neither() -> None:
    assert _pick(None, None, _at(5, 3)) == (3, "cloud")


def test_mqtt_counts_up_to_its_limit() -> None:
    assert _pick(None, _at(30, 2), None) == (2, "mqtt")
    assert _pick(None, _at(31, 2), None) is None


def test_a_stale_mqtt_value_gives_way_to_fresh_http() -> None:
    assert _pick(None, _at(31, 2), _at(10, 3)) == (3, "cloud")


def test_http_counts_up_to_its_limit() -> None:
    assert _pick(None, None, _at(60, 3)) == (3, "cloud")
    assert _pick(None, None, _at(61, 3)) is None


def test_a_time_in_the_future_counts_only_within_the_limit() -> None:
    """The cloud's clock may run ahead; that must not keep a value fresh forever."""
    assert _pick(None, None, _at(-60, 3)) == (3, "cloud")
    assert _pick(None, None, _at(-61, 3)) is None


def test_nothing_gives_none() -> None:
    assert _pick(None, None, None) is None
