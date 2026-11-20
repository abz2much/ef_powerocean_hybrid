"""What the MQTT connection logs, so a stale connection can be explained.

On 20 Nov 2026 the owner's commands reached the system minutes late and no
acknowledgement came back, with nothing in the log; a reload fixed it.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from unittest.mock import MagicMock, patch

import paho.mqtt.client as paho
import pytest

from custom_components.ef_powerocean_hybrid.cloud import mqtt as mqtt_module
from custom_components.ef_powerocean_hybrid.cloud.mqtt import PowerOceanMqtt
from tests.cloud.test_mqtt import (
    CREDENTIALS,
    SET_REPLY_TOPIC,
    SN,
    USER_ID,
    _callbacks,
)

LOGGER_NAME = "custom_components.ef_powerocean_hybrid.cloud"


@pytest.fixture
def connection() -> PowerOceanMqtt:
    mqtt = PowerOceanMqtt(CREDENTIALS, SN, USER_ID, _callbacks())
    mqtt.client = MagicMock()
    cast(Any, mqtt.client.publish).return_value = MagicMock(rc=paho.MQTT_ERR_SUCCESS)
    return mqtt


def test_a_disconnect_is_a_warning(
    connection: PowerOceanMqtt, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, LOGGER_NAME):
        connection._on_disconnect(
            connection.client,
            None,
            None,
            MagicMock(__str__=lambda _: "Keep alive timeout"),
            None,
        )

    assert "MQTT disconnected: Keep alive timeout" in caplog.text


def test_the_client_reports_disconnects_and_its_own_log() -> None:
    mqtt = PowerOceanMqtt(CREDENTIALS, SN, USER_ID, _callbacks())

    assert mqtt.client.on_disconnect == mqtt._on_disconnect
    assert mqtt.client.logger is not None
    assert mqtt.client.logger.name == f"{LOGGER_NAME}.paho"


def test_a_connect_is_logged(
    connection: PowerOceanMqtt, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, LOGGER_NAME):
        connection._on_connect(
            connection.client, None, None, MagicMock(is_failure=False), None
        )

    assert "MQTT connected" in caplog.text


def test_a_command_not_sent_is_a_warning(
    connection: PowerOceanMqtt, caplog: pytest.LogCaptureFixture
) -> None:
    cast(Any, connection.client.publish).return_value = MagicMock(
        rc=paho.MQTT_ERR_NO_CONN
    )

    with caplog.at_level(logging.WARNING, LOGGER_NAME):
        connection.set_schedule_enabled(14, 0, seq=4321)

    assert "MQTT command 4321 not sent" in caplog.text


def test_a_command_sent_is_logged_with_its_seq(
    connection: PowerOceanMqtt, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, LOGGER_NAME):
        connection.set_schedule_enabled(14, 0, seq=4321)

    assert "MQTT sent command 4321" in caplog.text


def test_a_message_that_cannot_be_read_does_not_stop_the_connection(
    connection: PowerOceanMqtt, caplog: pytest.LogCaptureFixture
) -> None:
    """An error raised into paho stops its thread: nothing more arrives."""
    with (
        patch.object(mqtt_module, "split_message", side_effect=KeyError("x")),
        caplog.at_level(logging.WARNING, LOGGER_NAME),
    ):
        connection._on_message(
            connection.client, None, MagicMock(topic=SET_REPLY_TOPIC, payload=b"x")
        )

    assert "MQTT message could not be read" in caplog.text


def test_a_reply_is_logged_with_its_seq(
    connection: PowerOceanMqtt, caplog: pytest.LogCaptureFixture
) -> None:
    part = MagicMock(cmd_func=254, cmd_id=17, seq=4321, content=b"")
    with (
        patch.object(mqtt_module, "split_message", return_value=[part]),
        patch.object(mqtt_module, "schedule_list", return_value=None),
        patch.object(mqtt_module, "schedule_single", return_value=None),
        caplog.at_level(logging.DEBUG, LOGGER_NAME),
    ):
        connection._on_message(
            connection.client, None, MagicMock(topic=SET_REPLY_TOPIC, payload=b"x")
        )

    assert "MQTT reply 254/17 seq 4321" in caplog.text


def test_connected_asks_paho(connection: PowerOceanMqtt) -> None:
    cast(Any, connection.client.is_connected).return_value = False
    assert connection.connected is False
    cast(Any, connection.client.is_connected).return_value = True
    assert connection.connected is True
