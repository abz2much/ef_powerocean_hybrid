"""A fake EcoFlow cloud API and the model-86 response, for the cloud tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "response_model86.json"
CLOUD_SN = "SN_MAIN"
SERIAL = "HJ31ZAS2TEST0001"


class FakeApi:
    """Stands in for EcoflowApi: counts calls, fails as told."""

    def __init__(
        self, *, login: Exception | None = None, fetch: list[Any] | None = None
    ) -> None:
        self.sn = CLOUD_SN
        self.variant = "86"
        self.token: str | None = None
        self.logins = 0
        self._login_error = login
        # Each fetch takes the next item: an exception to raise, or a response.
        self._fetches = list(fetch or [])

    async def async_authorize(self) -> None:
        self.logins += 1
        if self._login_error is not None:
            raise self._login_error
        self.token = "token"

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
