"""The setup form's check of a cloud login: the account must know the inverter."""

from __future__ import annotations

from custom_components.ef_powerocean_hybrid.cloud.api import (
    AuthenticationError,
    EcoflowApiError,
    EcoflowConnectionError,
)
from custom_components.ef_powerocean_hybrid.cloud.coordinator import async_check_cloud
from tests.cloud.fakes import FakeApi, model86_response


async def test_a_login_that_reads_the_inverter_passes() -> None:
    assert await async_check_cloud(FakeApi(fetch=[model86_response()])) is None


async def test_a_rejected_login_is_invalid_auth() -> None:
    api = FakeApi(login=AuthenticationError("bad password"))
    assert await async_check_cloud(api) == "invalid_auth"


async def test_an_unreachable_cloud_cannot_connect() -> None:
    api = FakeApi(login=EcoflowConnectionError("down"))
    assert await async_check_cloud(api) == "cloud_cannot_connect"


async def test_an_account_without_this_inverter_is_refused() -> None:
    api = FakeApi(fetch=[EcoflowApiError("device not found")])
    assert await async_check_cloud(api) == "cloud_unknown_device"


async def test_a_fetch_that_cannot_connect_cannot_connect() -> None:
    api = FakeApi(fetch=[EcoflowConnectionError("down")])
    assert await async_check_cloud(api) == "cloud_cannot_connect"
