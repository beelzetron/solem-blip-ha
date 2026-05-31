"""Pytest configuration and fixtures for the Solem BL-IP integration."""

from unittest.mock import MagicMock

import pytest
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


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONTROLLER_MAC_ADDRESS: "Solem BL-IP (AA:BB:CC:DD:EE:FF)",
            NUM_STATIONS: 2,
        },
        options={
            DEFAULT_SCAN_INTERVAL: 60,
            BLUETOOTH_TIMEOUT: BLUETOOTH_DEFAULT_TIMEOUT,
            SOLEM_API_MOCK: "true",
        },
        unique_id="AA:BB:CC:DD:EE:FF",
    )


@pytest.fixture
def mock_solem_client() -> MagicMock:
    """Create a mock SolemClient."""
    client = MagicMock()
    client.max_station_num = 2
    client.mock = True
    client.get_status = MagicMock(return_value={
        "controller_state": "On",
        "is_watering": False,
        "battery_voltage": 90,
        "battery_level": 5,
        "battery_low": False,
        "station_num": None,
        "remaining_seconds": None,
    })
    client.sprinkle_station_x_for_y_minutes = MagicMock()
    client.stop_manual_sprinkle = MagicMock()
    client.turn_on = MagicMock()
    client.turn_off_permanent = MagicMock()
    client.connect = MagicMock()
    client.disconnect = MagicMock()
    return client
