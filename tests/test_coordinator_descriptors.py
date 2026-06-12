"""Coordinator entity descriptor tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
            valves = [d for d in data if d["device_type"] == "STATION_VALVE"]

            assert len(state_sensors) == 3
            assert len(sprinkles) == 2
            assert len(remaining) == 2
            assert len(valves) == 2
            assert valves[1]["station_num"] == 2

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
            off_days_remaining = [
                d
                for d in data
                if d["device_type"] == "CONTROLLER_OFF_DAYS_REMAINING_SENSOR"
            ]

            assert len(battery) == 1
            assert len(voltage) == 1
            assert len(low) == 1
            assert len(off_days_remaining) == 1
            assert off_days_remaining[0]["state"] == 0

    async def test_control_entities_are_present(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Controller buttons and number controls are present."""
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
            off_days_button = [
                d for d in data if d["device_type"] == "OFF_DAYS_BUTTON"
            ]
            off_days_number = [
                d for d in data if d["device_type"] == "CONTROLLER_OFF_DAYS_NUMBER"
            ]

            assert len(stop) == 1
            assert len(on) == 1
            assert len(off) == 1
            assert len(off_days_button) == 1
            assert len(off_days_number) == 1
            assert off_days_number[0]["value"] == 1

    async def test_irrigation_programs_are_surfaced(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Program schedule sensors are present after config read."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()
            await coordinator.schedule_coordinator.async_refresh()
            data = await coordinator.async_update_all_sensors(fetch_status=False)

            next_starts = [
                d for d in data if d["device_type"] == "PROGRAM_NEXT_START_SENSOR"
            ]
            schedules = [
                d for d in data if d["device_type"] == "PROGRAM_SCHEDULE_SENSOR"
            ]

            running = [
                d for d in data if d["device_type"] == "PROGRAM_RUNNING_SENSOR"
            ]
            start_buttons = [
                d for d in data if d["device_type"] == "PROGRAM_START_BUTTON"
            ]

            assert len(next_starts) == 3
            assert len(schedules) == 3
            assert len(running) == 3
            assert len(start_buttons) == 3
            assert start_buttons[1]["program_num"] == 2
            assert start_buttons[1]["translation_placeholders"] == {
                "program_name": "Programma B"
            }
            assert next_starts[0]["translation_placeholders"] == {
                "program_name": "Programma A"
            }
            assert schedules[0]["state"] == (
                "17:40 · Station 1 20 min, Station 5 30 min"
            )
            assert schedules[0]["attributes"]["enabled_start_count"] == 1
            assert coordinator.irrigation_programs[0]["name"] == "Programma A"

    async def test_last_time_sync_sensor_is_present(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Last time sync diagnostic is present and unknown until sync."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()
            data = await coordinator.async_update_all_sensors(fetch_status=False)

            sync = [d for d in data if d["device_type"] == "LAST_TIME_SYNC_SENSOR"]
            assert len(sync) == 1
            assert sync[0]["state"] is None
