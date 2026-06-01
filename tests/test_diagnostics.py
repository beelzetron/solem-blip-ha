"""Diagnostics tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import RuntimeData
from custom_components.solem_blip.const import CONTROLLER_MAC_ADDRESS
from custom_components.solem_blip.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_redact_mac_and_include_runtime_state(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Diagnostics redact the configured MAC and expose useful coordinator state."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.firmware_version = "5.1.7"
    coordinator.num_stations = 6
    coordinator.battery_level = 4
    coordinator.battery_voltage = 87
    coordinator.battery_low = False
    coordinator._irrigation_active = True
    coordinator._is_watering = True
    coordinator.active_station_num = 2
    coordinator.active_program_num = 3
    coordinator.watering_origin = "schedule"
    coordinator.remaining_seconds = 90
    coordinator.station_names = {1: "Zone 1", 2: "Zone 2"}
    coordinator._firmware_retry_after = 1.0
    coordinator._station_names_retry_after = 2.0
    coordinator._metadata_task = MagicMock()
    coordinator._metadata_task.done.return_value = False
    coordinator._last_successful_poll_at = 100.0
    coordinator.irrigation_programs = {
        0: {"name": "Programma A"},
        2: {"name": "Programma C"},
    }
    coordinator._program_display_name.return_value = "Programma C"
    coordinator._irrigation_config_retry_after = 3.0
    coordinator._irrigation_config_refresh_after = 4.0
    mock_config_entry.runtime_data = RuntimeData(coordinator, MagicMock())

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["config_entry"][CONTROLLER_MAC_ADDRESS] == "**REDACTED**"
    assert result["firmware_version"] == "5.1.7"
    assert result["station_count"] == 6
    assert result["station_names_loaded"] == 2
    assert result["program_names"]["A"] == "Programma A"
    assert result["program_names"]["C"] == "Programma C"
    assert result["last_update_success"] is True
    assert result["irrigation"]["is_watering"] is True
    assert result["irrigation"]["station"] == 2
    assert result["irrigation"]["active_program"] == 3
    assert result["irrigation"]["active_program_name"] == "Programma C"
    assert result["irrigation"]["watering_origin"] == "schedule"
    assert result["metadata_retry_after"]["station_names"] == 2.0
    assert result["metadata_task"] == {"active": True, "finished": False}
    assert result["schedule_read"]["program_count"] == 2
