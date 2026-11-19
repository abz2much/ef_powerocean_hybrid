"""How a number is shown: the same for a Modbus sensor and a joined one.

A joined value keeps the entity ID of the Modbus sensor it replaces, so its
state must read the same, or dashboards and templates reading it would change.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)

from .const import UNIT_OF_RATIO

VALUE_PRECISION: Final = {
    UNIT_OF_RATIO: 0,
    UnitOfPower.WATT: 0,
    UnitOfEnergy.KILO_WATT_HOUR: 2,
    UnitOfTemperature.CELSIUS: 1,
    UnitOfFrequency.HERTZ: 2,
    UnitOfElectricPotential.VOLT: 1,
    UnitOfElectricCurrent.AMPERE: 2,
}


def shown_number(value: float, unit: str | None) -> int | float:
    """Round *value* for its unit: a whole number where the unit has no decimals."""
    number = float(value)
    precision = VALUE_PRECISION.get(unit) if unit is not None else None
    if precision is None:
        return int(number)
    return round(number, precision) if precision else round(number)
