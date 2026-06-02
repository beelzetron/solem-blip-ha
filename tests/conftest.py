"""Pytest configuration and fixtures for the Solem BL-IP integration."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.const import (
    BLUETOOTH_DEFAULT_TIMEOUT,
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NUM_STATIONS,
    SOLEM_API_MOCK,
)
from custom_components.solem_blip.coordinator import SolemCoordinator


MOCK_IRRIGATION_PROGRAMS = {
    0: {
        "name": "Programma A",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 4,
        "week_days": 0x7F,
        "period_length": 2,
        "synchro_day": 0,
        "period_start_date": None,
        "start_times": [1060, None, None, None, None, None, None, None],
        "station_durations": [1200, 0, 0, 0, 1800, 0],
    },
    1: {
        "name": "Programma B",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 4,
        "week_days": 0x7F,
        "period_length": 2,
        "synchro_day": 0,
        "period_start_date": None,
        "start_times": [None] * 8,
        "station_durations": [0, 0],
    },
    2: {
        "name": "Programma C",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 4,
        "week_days": 0x11,
        "period_length": 3,
        "synchro_day": 1,
        "period_start_date": date(2026, 6, 1),
        "start_times": [270, None, None, None, None, None, None, None],
        "station_durations": [0, 1500, 1500, 0],
    },
}


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
            NUM_STATIONS: 2,
        },
        options={
            DEFAULT_SCAN_INTERVAL: 60,
            BLUETOOTH_TIMEOUT: BLUETOOTH_DEFAULT_TIMEOUT,
            SOLEM_API_MOCK: "true",
        },
        unique_id="AA:BB:CC:DD:EE:FF",
    )


def create_mock_solem_client(station_num: int = 2) -> MagicMock:
    """Create a mock SolemClient with configurable station count."""
    client = MagicMock()
    client.max_station_num = station_num
    client.mock = True
    client.get_status = AsyncMock(return_value={
        "controller_state": "On",
        "is_watering": False,
        "battery_voltage": 90,
        "battery_level": 5,
        "battery_low": False,
        "station_num": None,
        "remaining_seconds": None,
        "active_program": None,
        "watering_origin": None,
    })
    client.get_firmware_version = AsyncMock(return_value={
        "major": 5,
        "minor": 1,
        "patch": 5,
        "raw_hex": "5.1.5",
    })
    client.get_station_names = AsyncMock(return_value={
        station: f"Zone {station}" for station in range(1, station_num + 1)
    })
    client.get_irrigation_config = AsyncMock(return_value=MOCK_IRRIGATION_PROGRAMS)
    client.set_time = AsyncMock()
    client.sprinkle_station_x_for_y_minutes = AsyncMock()
    client.stop_manual_sprinkle = AsyncMock()
    client.turn_on = AsyncMock()
    client.turn_off_permanent = AsyncMock()
    client.turn_off_x_days = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    return client


@pytest.fixture
def mock_solem_client() -> MagicMock:
    """Create a mock SolemClient."""
    return create_mock_solem_client(2)


@pytest.fixture
def mock_ble_device_resolver() -> Mock:
    """Create a mock BLE device resolver."""
    mock_device = MagicMock()
    mock_device.address = "AA:BB:CC:DD:EE:FF"
    mock_device.name = "Solem BL-IP"
    return Mock(return_value=mock_device)


@pytest.fixture
async def coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_solem_client
) -> SolemCoordinator:
    """Create a coordinator with mocked dependencies."""
    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        return_value=Mock(address="AA:BB:CC:DD:EE:FF", name="Solem BL-IP"),
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        return coordinator
