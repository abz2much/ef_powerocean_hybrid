"""The integration's names, so it can run beside the upstream integration."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.ef_powerocean_hybrid import const, control_test
from custom_components.ef_powerocean_hybrid.coordinator import state_store

HYBRID = "ef_powerocean_hybrid"


def test_the_domain_is_the_hybrid() -> None:
    assert const.DOMAIN == HYBRID


def test_the_state_store_is_its_own(hass: HomeAssistant) -> None:
    assert state_store(hass, "entry").key == f"{HYBRID}.entry.state"


def test_every_event_is_its_own() -> None:
    events = [v for k, v in vars(const).items() if k.startswith("EVENT_")]
    assert events
    assert all(event.startswith(f"{HYBRID}_") for event in events)


def test_control_test_reports_go_to_its_own_folder() -> None:
    assert control_test.REPORT_DIRECTORY == HYBRID


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_actions_are_its_own(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, HYBRID, {})
    assert hass.services.has_service(HYBRID, "set_battery_command")
