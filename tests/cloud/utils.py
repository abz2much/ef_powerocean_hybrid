"""Helpers for the cloud parser tests, from powerocean_single_phase's tests/utils."""

from typing import Any

from custom_components.ef_powerocean_hybrid.cloud.parse import parse

SN = "SN_INVERTERBOX01"


def parse_structure(response: dict, variant: str, sn: str = SN) -> dict[str, Any]:
    """Return the datapoints of a response by unique_id, as first seen."""
    return dict(parse(response, sn, variant).first())


def parse_values(response: dict, variant: str, sn: str = SN) -> dict[str, Any]:
    """Return the values of a response, as the coordinator returns them."""
    return parse(response, sn, variant).values()
