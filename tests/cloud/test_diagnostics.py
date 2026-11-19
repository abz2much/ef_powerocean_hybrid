"""Diagnostics never show the account, the serials or the inverter's address."""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.cloud.conftest import SetUp
from tests.cloud.fakes import CLOUD_SN, SERIAL, FakeApi, model86_response

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SECRETS = ("owner@example.com", "secret", CLOUD_SN, SERIAL, "127.0.0.1")


async def test_a_hybrid_entry_hides_the_account_serials_and_address(
    hass: HomeAssistant, hybrid_entry: MockConfigEntry, set_up_hybrid: SetUp
) -> None:
    await set_up_hybrid(hybrid_entry, FakeApi(fetch=[model86_response()]))

    text = json.dumps(
        await async_get_config_entry_diagnostics(hass, hybrid_entry), default=str
    )

    for secret in SECRETS:
        assert secret not in text, secret
