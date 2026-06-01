"""Diagnostics support for the Solem BL-IP integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .config_entry import MyConfigEntry
from .const import CONTROLLER_MAC_ADDRESS

_TO_REDACT = {CONTROLLER_MAC_ADDRESS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: MyConfigEntry
) -> dict[str, Any]:
    """Return redacted runtime diagnostics for a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    return {
        "config_entry": async_redact_data(dict(config_entry.data), _TO_REDACT),
        "available": coordinator.last_update_success,
        "firmware_version": coordinator.firmware_version,
        "station_count": coordinator.num_stations,
        "battery": {
            "level": coordinator.battery_level,
            "voltage_raw": coordinator.battery_voltage,
            "low": coordinator.battery_low,
        },
        "irrigation": {
            "active": coordinator._irrigation_active,
            "station": coordinator.active_station_num,
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
