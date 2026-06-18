"""Custom services for Solem BL-IP schedule management."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr
from solem_blip_ble import IrrigationProgram

from .config_entry import MyConfigEntry
from .const import DOMAIN, PROGRAM_LABELS

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

SERVICE_REFRESH_PROGRAMS = "refresh_programs"
SERVICE_SET_PROGRAM = "set_program"

ATTR_CYCLE = "cycle"
ATTR_DEVICE_ID = "device_id"
ATTR_INTER_STATION_DELAY = "inter_station_delay"
ATTR_NAME = "name"
ATTR_PERIOD_LENGTH = "period_length"
ATTR_PERIOD_START_DATE = "period_start_date"
ATTR_PROGRAM = "program"
ATTR_START_TIMES = "start_times"
ATTR_STATION_DURATIONS = "station_durations"
ATTR_SYNCHRO_DAY = "synchro_day"
ATTR_WATER_BUDGET = "water_budget"
ATTR_WEEK_DAYS = "week_days"

_CYCLES = {
    "custom": 0,
    "even": 1,
    "odd": 2,
    "odd_31": 3,
    "periodic": 4,
}
_WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

_COMMON_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
    }
)
_SET_PROGRAM_SCHEMA = _COMMON_SERVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_PROGRAM): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_START_TIMES): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_STATION_DURATIONS): dict,
        vol.Optional(ATTR_CYCLE, default="custom"): vol.In(tuple(_CYCLES)),
        vol.Optional(ATTR_WEEK_DAYS, default=list(_WEEKDAYS)): vol.All(
            cv.ensure_list, list
        ),
        vol.Optional(ATTR_PERIOD_LENGTH, default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=255)
        ),
        vol.Optional(ATTR_SYNCHRO_DAY, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=255)
        ),
        vol.Optional(ATTR_PERIOD_START_DATE): cv.date,
        vol.Optional(ATTR_INTER_STATION_DELAY, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Optional(ATTR_WATER_BUDGET, default=100): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register Solem BL-IP services."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_PROGRAM):
        return

    async def handle_set_program(call: ServiceCall) -> None:
        coordinator = _coordinator_from_device(hass, call.data[ATTR_DEVICE_ID])
        if coordinator._irrigation_active or coordinator._is_watering:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_program_while_watering",
            )

        program_index = int(call.data[ATTR_PROGRAM]) - 1
        program = _program_from_service_data(
            call.data,
            num_stations=coordinator.num_stations,
        )
        try:
            await coordinator.set_irrigation_program(program_index, program)
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_program_failed",
                translation_placeholders={
                    "program_name": f"Program {PROGRAM_LABELS[program_index]}"
                },
            ) from err

    async def handle_refresh_programs(call: ServiceCall) -> None:
        coordinator = _coordinator_from_device(hass, call.data[ATTR_DEVICE_ID])
        coordinator.request_schedule_refresh()
        await coordinator.schedule_coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PROGRAM,
        handle_set_program,
        schema=_SET_PROGRAM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_PROGRAMS,
        handle_refresh_programs,
        schema=_COMMON_SERVICE_SCHEMA,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove Solem BL-IP services."""
    for service in (SERVICE_SET_PROGRAM, SERVICE_REFRESH_PROGRAMS):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)


def _coordinator_from_device(hass: HomeAssistant, device_id: str) -> SolemCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="service_device_not_found",
        )

    macs = {
        identifier[1]
        for identifier in device.identifiers
        if len(identifier) == 2 and identifier[0] == DOMAIN
    }
    for entry in hass.config_entries.async_entries(DOMAIN):
        config_entry = cast(MyConfigEntry, entry)
        runtime_data = config_entry.runtime_data
        if runtime_data and runtime_data.coordinator.controller_mac_address in macs:
            return runtime_data.coordinator

    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="service_device_not_found",
    )


def _parse_start_time(value: str) -> int:
    try:
        hours_text, minutes_text = value.split(":", 1)
        hours = int(hours_text)
        minutes = int(minutes_text)
    except ValueError as exc:
        raise vol.Invalid("start_times must use HH:MM") from exc
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise vol.Invalid("start_times must use HH:MM between 00:00 and 23:59")
    return hours * 60 + minutes


def _week_days_mask(values: list[Any]) -> int:
    mask = 0
    for value in values:
        if isinstance(value, int):
            day = value
        else:
            key = str(value).lower()
            if key not in _WEEKDAYS:
                raise vol.Invalid(f"invalid weekday: {value}")
            day = _WEEKDAYS[key]
        if not 0 <= day <= 6:
            raise vol.Invalid("weekday integers must be between 0 and 6")
        mask |= 1 << day
    return mask


def _station_durations(value: dict[Any, Any], *, num_stations: int) -> list[int]:
    durations = [0] * num_stations
    for station_raw, seconds_raw in value.items():
        station = int(station_raw)
        seconds = int(seconds_raw)
        if not 1 <= station <= num_stations:
            raise vol.Invalid(f"station must be between 1 and {num_stations}")
        if not 0 <= seconds <= 0xFFFFFF:
            raise vol.Invalid("station duration must be between 0 and 16777215")
        durations[station - 1] = seconds
    return durations


def _program_from_service_data(
    data: dict[str, Any],
    *,
    num_stations: int,
) -> IrrigationProgram:
    start_times: list[int | None] = [
        _parse_start_time(value) for value in data[ATTR_START_TIMES]
    ]
    if len(start_times) > 8:
        raise vol.Invalid("start_times must contain at most 8 entries")
    start_times.extend([None] * (8 - len(start_times)))

    period_start_date = data.get(ATTR_PERIOD_START_DATE)
    if isinstance(period_start_date, str):
        period_start_date = date.fromisoformat(period_start_date)

    return {
        "name": str(data[ATTR_NAME]),
        "inter_station_delay": int(data[ATTR_INTER_STATION_DELAY]),
        "water_budget": int(data[ATTR_WATER_BUDGET]),
        "cycle": _CYCLES[str(data[ATTR_CYCLE])],
        "week_days": _week_days_mask(list(data[ATTR_WEEK_DAYS])),
        "period_length": int(data[ATTR_PERIOD_LENGTH]),
        "synchro_day": int(data[ATTR_SYNCHRO_DAY]),
        "period_start_date": period_start_date,
        "start_times": start_times,
        "station_durations": _station_durations(
            data[ATTR_STATION_DURATIONS],
            num_stations=num_stations,
        ),
    }
