"""How fast a cloud total may rise: the Modbus integration's energy processor's way."""

from __future__ import annotations

import pytest

from custom_components.ef_powerocean_hybrid import const
from custom_components.ef_powerocean_hybrid.cloud_sensor import total_limit_w

CHARGED = const.CONF_MAX_BATTERY_CHARGED_POWER
DISCHARGED = const.CONF_MAX_BATTERY_DISCHARGED_POWER


def test_with_modbus_the_modbus_coordinators_limits_are_used() -> None:
    limits = {CHARGED: 5106, DISCHARGED: 8160}

    assert total_limit_w(CHARGED, {}, limits, pack_count=0) == 5106
    assert total_limit_w(DISCHARGED, {}, limits, pack_count=0) == 8160


def test_cloud_only_counts_each_pack_as_the_largest_module() -> None:
    """As the blueprint does until the capacity is read: 8,030 Wh at the
    highest rate of any module (const.py BATTERY_MODULES)."""
    charged = total_limit_w(CHARGED, {}, None, pack_count=2)
    discharged = total_limit_w(DISCHARGED, {}, None, pack_count=2)

    assert charged == round(2 * 8030 * 4100 / 8030)
    assert discharged == round(2 * 8030 * 4080 / 5000)


def test_no_packs_reported_gives_0_w_as_in_the_blueprint() -> None:
    assert total_limit_w(CHARGED, {}, None, pack_count=0) == 0


@pytest.mark.parametrize(
    ("options", "limit"),
    (({}, const.DEFAULT_MAX_SOLAR_POWER), ({const.CONF_MAX_SOLAR_POWER: 6000}, 6000)),
)
def test_solar_uses_max_solar_power_or_its_default(options: dict, limit: int) -> None:
    solar = total_limit_w(const.CONF_MAX_SOLAR_POWER, options, None, pack_count=2)

    assert solar == limit
