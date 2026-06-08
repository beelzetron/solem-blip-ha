"""Coordinator manual irrigation tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.api import APIConnectionError
from custom_components.solem_blip.coordinator import SolemCoordinator

from conftest import create_mock_solem_client


def watering_status(station: int = 1, remaining_seconds: int = 60) -> dict:
    """Return a device status payload for active station watering."""
    return {
        "controller_state": "On",
        "controller_off_mode": "on",
        "controller_off_days_remaining": 0,
        "is_watering": True,
        "battery_voltage": 90,
        "battery_level": 5,
        "battery_low": False,
        "station_num": station,
        "remaining_seconds": remaining_seconds,
        "active_program": None,
        "watering_origin": "manual",
    }


@pytest.mark.asyncio
class TestStartStopButtonBehavior:
    """Test start/stop button behavior and error handling."""

    async def test_start_irrigation_calls_solem_client(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Station button calls coordinator.start_irrigation."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()
            mock_solem_client.get_status.return_value = watering_status(
                station=1, remaining_seconds=300
            )

            await coordinator.start_irrigation(station=1, minutes=5)

            mock_solem_client.sprinkle_station_x_for_y_minutes.assert_called_once_with(
                1, 5
            )
            mock_solem_client.get_status.assert_awaited()
            assert coordinator.active_station_num == 1

    async def test_stop_irrigation_calls_solem_client(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Stop button calls coordinator.stop_irrigation."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            await coordinator.start_irrigation(station=1, minutes=5)
            mock_solem_client.sprinkle_station_x_for_y_minutes.reset_mock()

            await coordinator.stop_irrigation()

            mock_solem_client.stop_manual_sprinkle.assert_called_once()

    async def test_start_program_calls_solem_client_and_refreshes_status(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Program button calls coordinator.start_program."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()
            mock_solem_client.get_status.return_value = {
                "controller_state": "On",
                "controller_off_mode": "on",
                "controller_off_days_remaining": 0,
                "is_watering": True,
                "battery_voltage": 90,
                "battery_level": 5,
                "battery_low": False,
                "station_num": 1,
                "remaining_seconds": 120,
                "active_program": 2,
                "watering_origin": "program",
            }

            await coordinator.start_program(2)

            mock_solem_client.run_program_x.assert_called_once_with(2)
            mock_solem_client.get_status.assert_awaited()
            assert coordinator._irrigation_active is False
            assert coordinator._is_watering is True
            assert coordinator.active_program_num == 2
            assert coordinator.watering_origin == "program"

    async def test_failed_start_program_resets_active_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Failed program start leaves device-derived state untouched."""
        mock_client = create_mock_solem_client(2)
        mock_client.run_program_x = AsyncMock(
            side_effect=APIConnectionError("Failed to start")
        )

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            with pytest.raises(HomeAssistantError) as exc_info:
                await coordinator.start_program(2)
            assert exc_info.value.translation_key == "start_program_failed"
            assert exc_info.value.translation_placeholders == {
                "program_name": "Program B"
            }
            assert coordinator._irrigation_active is False
            assert coordinator.active_program_num is None
            assert coordinator.watering_origin is None

    async def test_api_connection_error_is_surfaces_as_home_assistant_error(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """APIConnectionError is surfaced as HomeAssistantError."""
        mock_client = MagicMock()
        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(
            side_effect=APIConnectionError("Connection failed")
        )
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            with pytest.raises(HomeAssistantError) as exc_info:
                await coordinator.start_irrigation(station=1, minutes=5)
            assert exc_info.value.translation_key == "start_irrigation_failed"


@pytest.mark.asyncio
class TestIrrigationMonitorLifecycle:
    """Test monitor lifecycle from Task 4."""

    async def test_starting_stores_monitor_task_after_device_reports_watering(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Starting irrigation stores a monitor task after status reports watering."""
        mock_client = create_mock_solem_client(2)

        async def slow_sprinkle(*args, **kwargs):
            await asyncio.sleep(0.1)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(side_effect=slow_sprinkle)
        mock_client.get_status = AsyncMock(return_value=watering_status())

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            task = asyncio.create_task(coordinator.start_irrigation(1, 1))
            await asyncio.sleep(0.05)

            assert coordinator._irrigation_active is False
            assert coordinator._irrigation_monitor_task is None

            await task
            assert coordinator._irrigation_active is True
            assert coordinator._irrigation_monitor_task is not None
            await coordinator.async_shutdown()

    async def test_stop_cancels_monitor_task(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Stop irrigation cancels the stored task."""
        mock_client = create_mock_solem_client(2)

        async def quick_sprinkle(*args, **kwargs):
            pass

        async def slow_monitor(station, duration):
            await asyncio.sleep(10)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(side_effect=quick_sprinkle)
        mock_client.get_status = AsyncMock(return_value=watering_status())

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            coordinator._run_irrigation_monitor = slow_monitor

            start_task = asyncio.create_task(coordinator.start_irrigation(1, 1))
            await asyncio.sleep(0.01)

            assert coordinator._irrigation_monitor_task is not None
            assert not coordinator._irrigation_monitor_task.done()
            coordinator.active_program_num = 2
            coordinator.watering_origin = "program"

            await asyncio.wait_for(coordinator.stop_irrigation(), timeout=0.1)
            await start_task

            assert coordinator._irrigation_monitor_task is None
            assert coordinator._irrigation_active is False
            assert coordinator.active_station_num is None
            assert coordinator.remaining_seconds is None
            assert coordinator.active_program_num is None
            assert coordinator.watering_origin is None

    async def test_failed_start_resets_active_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Failed start resets _irrigation_active state."""
        mock_client = MagicMock()
        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(
            side_effect=APIConnectionError("Failed to start")
        )
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            with pytest.raises(HomeAssistantError) as exc_info:
                await coordinator.start_irrigation(1, 1)
            assert exc_info.value.translation_key == "start_irrigation_failed"

            assert coordinator._irrigation_active is False
            assert coordinator._irrigation_monitor_task is None

            mock_client.sprinkle_station_x_for_y_minutes = AsyncMock()
            mock_client.get_status = AsyncMock(return_value=watering_status())

            async def slow_monitor(station, duration):
                await asyncio.sleep(10)

            coordinator._run_irrigation_monitor = slow_monitor
            await coordinator.start_irrigation(1, 1)

            assert coordinator._irrigation_active is True
            assert coordinator._irrigation_monitor_task is not None

            await coordinator.async_shutdown()

    async def test_async_shutdown_cancels_monitor_task(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """async_shutdown cancels and awaits monitor task."""
        mock_client = create_mock_solem_client(2)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock()
        mock_client.get_status = AsyncMock(return_value=watering_status())

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            monitor_task_started = asyncio.Event()

            async def slow_monitor(station, duration):
                monitor_task_started.set()
                await asyncio.sleep(10)

            coordinator._run_irrigation_monitor = slow_monitor

            start_task = asyncio.create_task(coordinator.start_irrigation(1, 1))
            await monitor_task_started.wait()

            assert coordinator._irrigation_monitor_task is not None

            shutdown_task = asyncio.create_task(coordinator.async_shutdown())
            await asyncio.sleep(0.1)

            assert coordinator._irrigation_monitor_task is None
            await start_task
            await shutdown_task

    async def test_station_state_waits_for_device_status(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Station state is sourced from status after the BLE command completes."""
        mock_client = create_mock_solem_client(2)

        async def slow_sprinkle(*args, **kwargs):
            await asyncio.sleep(0.1)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(side_effect=slow_sprinkle)
        mock_client.get_status = AsyncMock(return_value=watering_status())

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            for station in coordinator.stations:
                station.state = "inactive"

            start_task = asyncio.create_task(coordinator.start_irrigation(1, 1))
            await asyncio.sleep(0.01)

            assert coordinator.stations[0].state == "inactive"

            await start_task
            assert coordinator.stations[0].state == "active"
            await coordinator.async_shutdown()
