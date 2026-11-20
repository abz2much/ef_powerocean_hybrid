"""The schedule card is served with the integration and loaded on every page."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.ef_powerocean_hybrid import const, frontend_card
from custom_components.ef_powerocean_hybrid.frontend_card import async_register_card


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_card_folder_is_served_and_loaded(hass: HomeAssistant) -> None:
    hass.http = Mock(async_register_static_paths=AsyncMock())
    hass.config.components.add("frontend")

    with patch.object(frontend_card, "add_extra_js_url") as add_url:
        await async_register_card(hass)

    ((paths,), _) = hass.http.async_register_static_paths.call_args
    (config,) = paths
    assert config.url_path == "/ef_powerocean_hybrid/frontend"
    folder = Path(config.path)
    assert folder.name == "frontend"
    assert (folder / "schedule-card.js").is_file()
    assert (folder / "schedule-logic.js").is_file()
    # Not cached for long: the card imports the logic without a version, so
    # a long cache could keep an old logic file after an update.
    assert config.cache_headers is False
    add_url.assert_called_once_with(
        hass, "/ef_powerocean_hybrid/frontend/schedule-card.js?v=0.3.0"
    )


async def test_nothing_is_registered_without_http_and_frontend(
    hass: HomeAssistant,
) -> None:
    hass.http = None  # type: ignore[assignment]  # as in a setup without http

    with patch.object(frontend_card, "add_extra_js_url") as add_url:
        await async_register_card(hass)

    add_url.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_integration_setup_registers_the_card(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.ef_powerocean_hybrid.async_register_card",
        new=AsyncMock(),
    ) as register:
        assert await async_setup_component(hass, const.DOMAIN, {})

    register.assert_awaited_once_with(hass)
