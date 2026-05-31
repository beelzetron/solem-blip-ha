"""Pytest configuration and fixtures for the Solem BL-IP integration."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
def hass() -> Generator[HomeAssistant]:
    """Create a Home Assistant instance."""
    with patch(
        "homeassistant.helpers.restore_state.RestoreStateData.async_get_instance",
        return_value=AsyncMock(),
    ):
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers.storage import Store

        hass = HomeAssistant("/tmp/test_homeassistant")

        mock_store = MagicMock(spec=Store)
        mock_store._async_poll_lock = asyncio.Lock()
        hass.helpers.storage.Store = MagicMock(return_value=mock_store)

        yield hass


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
    client.get_status = AsyncMock(return_value={
        "controller_state": "On",
        "is_watering": False,
        "battery_voltage": 90,
        "battery_level": 5,
        "battery_low": False,
        "station_num": None,
        "remaining_seconds": None,
    })
    client.sprinkle_station_x_for_y_minutes = AsyncMock()
    client.stop_manual_sprinkle = AsyncMock()
    client.turn_on = AsyncMock()
    client.turn_off_permanent = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    return client
