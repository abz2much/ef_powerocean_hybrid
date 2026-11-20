"""A fake EcoFlow cloud API and the model-86 response, for the cloud tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "response_model86.json"
CLOUD_SN = "SN_MAIN"
SERIAL = "HJ31ZAS2TEST0001"
USER_ID = "1234567890"
CREDENTIALS = {
    "url": "mqtt.example.com",
    "port": "8883",
    "certificateAccount": "app-account",
    "certificatePassword": "app-password",
}


class FakeApi:
    """Stands in for EcoflowApi: counts calls, fails as told."""

    def __init__(
        self, *, login: Exception | None = None, fetch: list[Any] | None = None
    ) -> None:
        self.sn = CLOUD_SN
        self.variant = "86"
        self.token: str | None = None
        # Set by the login, as EcoflowApi does; MQTT needs it.
        self.user_id: str | None = None
        self.login_user_id: str | None = USER_ID
        self.logins = 0
        # What async_mqtt_credentials answers: credentials or an exception.
        self.mqtt_credentials: Any = CREDENTIALS
        self.credential_requests = 0
        self._login_error = login
        # Each fetch takes the next item: an exception to raise, or a response.
        self._fetches = list(fetch or [])

    async def async_authorize(self) -> None:
        self.logins += 1
        if self._login_error is not None:
            raise self._login_error
        self.token = "token"
        self.user_id = self.login_user_id

    def recover(self) -> None:
        """Let later logins succeed: the cloud is back."""
        self._login_error = None

    async def async_mqtt_credentials(self) -> dict[str, Any]:
        self.credential_requests += 1
        if isinstance(self.mqtt_credentials, Exception):
            raise self.mqtt_credentials
        return self.mqtt_credentials

    def respond(self, item: Any) -> None:
        """Answer every later fetch with *item*."""
        self._fetches = [item]

    async def fetch_raw(self) -> dict[str, Any]:
        item = self._fetches.pop(0) if len(self._fetches) > 1 else self._fetches[0]
        if isinstance(item, Exception):
            raise item
        return item


def model86_response() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))
