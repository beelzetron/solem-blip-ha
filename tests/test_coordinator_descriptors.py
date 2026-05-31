"""Coordinator entity descriptor tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


@pytest.mark.asyncio
class TestEntitySetup:
    """Test entity setup for configured number of stations."""

    @pytest.fixture(autouse=True)
    def expected_lingering_timers(self) -> bool:
        """Allow lingering debouncer timers for entity setup tests."""
        return True

    async def test_entity_descriptors_include_all_stations(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Sensors/buttons are created for the configured number of stations."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            data = await coordinator.async_update_all_sensors()

            state_sensors = [d for d in data if d["device_type"] == "STATE_SENSOR"]
            sprinkles = [
                d for d in data if d["device_type"] == "SPRINKLE_BUTTON"
            ]
            remaining = [
                d for d in data if d["device_type"] == "REMAINING_SPRINKLE_SENSOR"
            ]

            assert len(state_sensors) == 3
            assert len(sprinkles) == 2
            assert len(remaining) == 2

            new_config = MockConfigEntry(
                domain=DOMAIN,
                data={
                    CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
                    NUM_STATIONS: 4,
                },
                options={
                    DEFAULT_SCAN_INTERVAL: 60,
                    BLUETOOTH_TIMEOUT: BLUETOOTH_DEFAULT_TIMEOUT,
                    SOLEM_API_MOCK: "true",
                },
                unique_id="AA:BB:CC:DD:EE:FF",
            )
            await coordinator.update_config(new_config)

            data = await coordinator.async_update_all_sensors()

            sprinkles = [
                d for d in data if d["device_type"] == "SPRINKLE_BUTTON"
            ]
            remaining = [
                d for d in data if d["device_type"] == "REMAINING_SPRINKLE_SENSOR"
            ]

            assert len(sprinkles) == 4
            assert len(remaining) == 4

    async def test_battery_entities_are_present(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Battery, battery voltage, and battery low sensors are present."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()
            data = await coordinator.async_update_all_sensors()

            battery = [d for d in data if d["device_type"] == "BATTERY_SENSOR"]
            voltage = [
                d for d in data if d["device_type"] == "BATTERY_VOLTAGE_SENSOR"
            ]
            low = [d for d in data if d["device_type"] == "BATTERY_LOW_SENSOR"]

            assert len(battery) == 1
            assert len(voltage) == 1
            assert len(low) == 1

    async def test_control_buttons_are_present(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Stop, on, and off buttons are present."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            data = await coordinator.async_update_all_sensors()

            stop = [d for d in data if d["device_type"] == "STOP_BUTTON"]
            on = [d for d in data if d["device_type"] == "ON_BUTTON"]
            off = [d for d in data if d["device_type"] == "OFF_BUTTON"]

            assert len(stop) == 1
            assert len(on) == 1
            assert len(off) == 1
