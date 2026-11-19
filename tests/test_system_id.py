"""Entities and the device are keyed by the system's serial."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ef_powerocean_hybrid.const import DOMAIN
from custom_components.ef_powerocean_hybrid.ids import system_id


def test_the_serial_is_the_key() -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="HJ31ZAS2TEST0001")
    assert system_id(entry) == "HJ31ZAS2TEST0001"


def test_an_entry_whose_serial_is_not_known_yet_uses_its_entry_id() -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="192.0.2.10:502")
    assert system_id(entry) == entry.entry_id


def test_an_entry_without_a_unique_id_uses_its_entry_id() -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=None)
    assert system_id(entry) == entry.entry_id
