"""Diagnostics support for the Solem BL-IP integration."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from solem_blip_ble import IrrigationProgram

from .config_entry import MyConfigEntry
from .const import CONTROLLER_MAC_ADDRESS, PROGRAM_LABELS
from .coordinator_polling import active_program_name

_TO_REDACT = {CONTROLLER_MAC_ADDRESS}


def _program_diagnostic_name(program: IrrigationProgram | None) -> str | None:
    """Return a trimmed program name for diagnostics, or None if unavailable."""
    if program is None:
        return None
    name = program.get("name", "").strip()
    return name or None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: MyConfigEntry
) -> dict[str, Any]:
    """Return redacted runtime diagnostics for a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    now = asyncio.get_running_loop().time()
    last_poll_age = None
    if coordinator._last_successful_poll_at is not None:
        last_poll_age = round(now - coordinator._last_successful_poll_at, 1)

    program_names = {
        PROGRAM_LABELS[index]: _program_diagnostic_name(
            coordinator.irrigation_programs.get(index)
        )
        for index in range(len(PROGRAM_LABELS))
    }

    return {
        "config_entry": async_redact_data(dict(config_entry.data), _TO_REDACT),
        "available": coordinator.last_update_success,
        "last_update_success": coordinator.last_update_success,
        "last_poll_age_seconds": last_poll_age,
        "firmware_version": coordinator.firmware_version,
        "station_count": coordinator.num_stations,
        "station_names_loaded": len(coordinator.station_names),
        "program_names": program_names,
        "battery": {
            "level": coordinator.battery_level,
            "voltage_raw": coordinator.battery_voltage,
            "low": coordinator.battery_low,
        },
        "irrigation": {
            "active": coordinator._irrigation_active,
            "is_watering": coordinator._is_watering,
            "station": coordinator.active_station_num,
            "active_program": coordinator.active_program_num,
            "active_program_name": active_program_name(coordinator),
            "watering_origin": coordinator.watering_origin,
            "remaining_seconds": coordinator.remaining_seconds,
        },
        "metadata_retry_after": {
            "firmware": coordinator._firmware_retry_after,
            "station_names": coordinator._station_names_retry_after,
        },
        "schedule_read": {
            "program_count": len(coordinator.irrigation_programs),
            "retry_after": coordinator._irrigation_config_retry_after,
            "refresh_after": coordinator._irrigation_config_refresh_after,
        },
    }
