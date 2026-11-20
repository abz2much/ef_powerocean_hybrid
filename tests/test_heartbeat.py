"""The heartbeat keeps the inverter following us for as long as it is running."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from custom_components.ef_powerocean_hybrid.heartbeat import Heartbeat
from custom_components.ef_powerocean_hybrid.modbus import ModbusClient


class FakeClient:
    """Fails with *failures* in turn, then accepts every write."""

    def __init__(self, *failures: BaseException) -> None:
        self.connected = True
        self.writes_allowed = True
        self._failures = list(failures)
        self.accepted = asyncio.Event()

    async def async_write(
        self,
        address: int,  # noqa: ARG002 - matches ModbusClient
        words: list[int],  # noqa: ARG002 - matches ModbusClient
        *,
        what: str,  # noqa: ARG002 - matches ModbusClient
    ) -> None:
        if self._failures:
            raise self._failures.pop(0)
        self.accepted.set()


def test_an_unexpected_error_does_not_stop_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_sleep = asyncio.sleep

    async def no_wait(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    client = FakeClient(RuntimeError("bug in one write"))

    async def run() -> Heartbeat:
        heartbeat = Heartbeat(cast(ModbusClient, client), scan_interval_s=5)
        heartbeat.start()
        await asyncio.wait_for(client.accepted.wait(), 5)
        await heartbeat.async_stop()
        return heartbeat

    heartbeat = asyncio.run(run())

    assert heartbeat.in_control


class CountingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    async def async_write(self, address: int, words: list[int], *, what: str) -> None:
        self.writes += 1
        await super().async_write(address, words, what=what)


def test_no_beat_while_the_control_logic_has_stopped_deciding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inverter would keep the last command for as long as we beat.

    A beat lets it go on with whatever it was last told, so it must stop when the
    logic that would change or end that command (guards, expiry) no longer runs.
    """
    real_sleep = asyncio.sleep

    async def no_wait(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    client = CountingClient()
    alive = False

    async def run() -> None:
        heartbeat = Heartbeat(
            cast(ModbusClient, client), scan_interval_s=5, alive=lambda: alive
        )
        heartbeat.start()
        for _ in range(20):
            await real_sleep(0)
        await heartbeat.async_stop()

    asyncio.run(run())
    assert client.writes == 0

    alive = True
    asyncio.run(run())
    assert client.writes >= 1
