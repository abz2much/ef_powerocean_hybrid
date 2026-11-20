"""Serve the schedule card with the integration and load it on every page.

The card (frontend/schedule-card.js) then appears in the dashboard's card
picker, with no Dashboards > Resources step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARD_URL: Final = f"/{DOMAIN}/frontend"
FOLDER: Final = Path(__file__).parent / "frontend"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card's folder and load the card, where Home Assistant can."""
    if hass.http is not None:
        # Not cached for long: the card imports its logic without a version.
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(FOLDER), cache_headers=False)]
        )
    if "frontend" in hass.config.components:
        # The version makes browsers fetch the card again after an update.
        version = (await async_get_integration(hass, DOMAIN)).version
        add_extra_js_url(hass, f"{CARD_URL}/schedule-card.js?v={version}")
