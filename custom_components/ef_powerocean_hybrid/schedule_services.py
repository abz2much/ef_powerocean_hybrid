"""Actions for the scheduled tasks: add, update and delete.

Add targets the inverter device, as there may be no schedule yet to point at.
Update and delete target a schedule switch, as entity actions of the switch
platform. All three need the option "Max schedule power (W)", except delete.
The actions of powerocean_single_phase (v1.0.1), MIT, (c) 2026 niltrip,
abz2much.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_platform_entity_service

from .cloud.schedules import DAY_BITS, TYPE_BY_KEY, ScheduleInputError, schedule_changes
from .const import DOMAIN
from .ids import system_id

if TYPE_CHECKING:
    from .cloud.coordinator import CloudCoordinator

SERVICE_ADD_SCHEDULE: Final = "add_schedule"
SERVICE_UPDATE_SCHEDULE: Final = "update_schedule"
SERVICE_DELETE_SCHEDULE: Final = "delete_schedule"

# The power is checked later: a number, whole watts, from 1 to the option
_POWER = vol.Coerce(float)
_TYPE = vol.In(list(TYPE_BY_KEY))
_DAYS = vol.All(cv.ensure_list, [vol.In(list(DAY_BITS))])

ADD_SCHEMA: Final = vol.Schema(
    {
        vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Required("type"): _TYPE,
        vol.Required("power"): _POWER,
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Optional("days"): _DAYS,
        vol.Optional("enabled", default=True): cv.boolean,
    }
)

# For the entity service: the schema of the target is added by Home Assistant
UPDATE_SCHEMA: Final[dict[Any, Any]] = {
    vol.Optional("type"): _TYPE,
    vol.Optional("power"): _POWER,
    vol.Optional("start"): cv.time,
    vol.Optional("end"): cv.time,
    vol.Optional("days"): _DAYS,
    vol.Optional("daily"): cv.boolean,
    vol.Optional("enabled"): cv.boolean,
}


def changes_from_service(data: dict[str, Any]) -> dict[str, Any]:
    """Return the arguments of the coordinator; wrong input is a service error."""
    try:
        return schedule_changes(data)
    except ScheduleInputError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key=err.key
        ) from err


def _cloud_for_device(hass: HomeAssistant, device_id: str) -> CloudCoordinator:
    """Return the cloud of the system whose inverter is the device."""
    device = dr.async_get(hass).async_get(device_id)
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        cloud: CloudCoordinator | None = entry.runtime_data.cloud
        if (
            cloud is not None
            and device is not None
            and (DOMAIN, system_id(entry)) in device.identifiers
        ):
            return cloud
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="schedule_target"
    )


@callback
def async_setup_schedule_services(hass: HomeAssistant) -> None:
    """Register the actions: add targets a device, the others a schedule switch."""

    async def add_schedule(call: ServiceCall) -> None:
        changes = changes_from_service(dict(call.data))
        for device_id in call.data["device_id"]:
            await _cloud_for_device(hass, device_id).async_add_schedule(**changes)

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_SCHEDULE, add_schedule, schema=ADD_SCHEMA
    )
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_UPDATE_SCHEDULE,
        entity_domain="switch",
        schema=UPDATE_SCHEMA,
        func="async_update_schedule",
    )
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_DELETE_SCHEDULE,
        entity_domain="switch",
        schema=None,
        func="async_delete_schedule",
    )
