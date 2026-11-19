"""The key of a system's entities and devices."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry


def system_id(entry: ConfigEntry) -> str:
    """Return the key of the system's entities and device: its serial.

    An entry whose serial could not be read yet keeps its host:port unique ID;
    its entry ID stands in until the serial is known.
    """
    unique_id = entry.unique_id
    return unique_id if unique_id and ":" not in unique_id else entry.entry_id
