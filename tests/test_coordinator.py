"""Tests for the Solem BL-IP Home Assistant integration."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.api import APIConnectionError
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
    client.sprinkle_station_x_for_y_minutes = AsyncMock()
    client.stop_manual_sprinkle = AsyncMock()
    client.turn_on = AsyncMock()
    client.turn_off_permanent = AsyncMock()
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


@pytest.mark.asyncio
async def test_async_init_does_not_block_on_ble_io(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Entity descriptors are built before the first BLE poll."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()

    mock_solem_client.connect.assert_not_awaited()
    mock_solem_client.get_status.assert_not_awaited()
    assert coordinator.data
    assert coordinator.last_update_success is False
    assert coordinator.controller.state is None
    assert all(station.state is None for station in coordinator.stations)
    assert coordinator.battery_low is None
    assert coordinator._remaining_seconds_for_station(1) is None


@pytest.mark.asyncio
async def test_async_update_data_raises_update_failed_on_ble_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """BLE poll errors mark coordinator updates as failed."""
    mock_solem_client.get_status.side_effect = APIConnectionError("Offline")

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()

        with pytest.raises(UpdateFailed, match="Offline"):
            await coordinator.async_update_data()


@pytest.mark.asyncio
class TestCoordinatorReconfiguration:
    """Test coordinator reconfiguration behavior (Task 3 regression)."""

    @pytest.fixture(autouse=True)
    def expected_lingering_timers(self) -> bool:
        """Allow lingering debouncer timers for config update tests."""
        return True

    async def test_update_config_rebuilds_solem_client_with_new_station_count(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Changing NUM_STATIONS rebuilds SolemClient with new max_station_num."""
        mock_client_1 = create_mock_solem_client(2)
        mock_client_2 = create_mock_solem_client(4)

        call_count = [0]

        def create_client(*args, **kwargs):
            call_count[0] += 1
            return mock_client_1 if call_count[0] == 1 else mock_client_2

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            side_effect=create_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            assert coordinator.num_stations == 2
            assert coordinator.api.max_station_num == 2

            old_api = coordinator.api

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

            assert coordinator.num_stations == 4
            assert coordinator.api is not old_api
            assert coordinator.api.max_station_num == 4

    async def test_update_config_regenerates_station_descriptors(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Station descriptors are regenerated for the new station count."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            assert len(coordinator.stations) == 2
            assert coordinator.stations[0].station_number == 1
            assert coordinator.stations[1].station_number == 2

            new_config = MockConfigEntry(
                domain=DOMAIN,
                data={
                    CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
                    NUM_STATIONS: 6,
                },
                options={
                    DEFAULT_SCAN_INTERVAL: 60,
                    BLUETOOTH_TIMEOUT: BLUETOOTH_DEFAULT_TIMEOUT,
                    SOLEM_API_MOCK: "true",
                },
                unique_id="AA:BB:CC:DD:EE:FF",
            )

            await coordinator.update_config(new_config)

            assert len(coordinator.stations) == 6
            assert coordinator.stations[0].station_number == 1
            assert coordinator.stations[5].station_number == 6


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

    async def test_device_metadata_is_surfaced(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Firmware and controller-provided station names are surfaced."""
        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()
            mock_config_entry.add_to_hass(hass)
            device_registry = dr.async_get(hass)
            device = device_registry.async_get_or_create(
                config_entry_id=mock_config_entry.entry_id,
                identifiers={(DOMAIN, coordinator.controller_mac_address)},
            )

            data = await coordinator.async_update_all_sensors()

            assert coordinator.firmware_version == "5.1.5"
            assert coordinator.controller.software_version == "5.1.5"
            assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}
            assert any(d["device_name"] == "Zone 1 Status" for d in data)
            assert any(d["device_name"] == "Zone 1 remaining time" for d in data)
            assert any(d["device_name"] == "Sprinkle Zone 1" for d in data)
            assert device_registry.async_get(device.id).sw_version == "5.1.5"


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

            await coordinator.start_irrigation(station=1, minutes=5)

            mock_solem_client.sprinkle_station_x_for_y_minutes.assert_called_once_with(
                1, 5
            )

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

    async def test_api_connection_error_is_surfaces_as_home_assistant_error(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """APIConnectionError is surfaced as HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

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

            with pytest.raises(HomeAssistantError, match="Connection failed"):
                await coordinator.start_irrigation(station=1, minutes=5)


@pytest.mark.asyncio
class TestIrrigationMonitorLifecycle:
    """Test monitor lifecycle from Task 4."""

    async def test_starting_stores_monitor_task_and_marks_active(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Starting irrigation stores a monitor task and marks active before command completes."""
        mock_client = create_mock_solem_client(2)

        async def slow_sprinkle(*args, **kwargs):
            await asyncio.sleep(0.1)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(side_effect=slow_sprinkle)

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

            assert coordinator._irrigation_active is True
            assert coordinator._irrigation_monitor_task is not None

            await task

    async def test_stop_cancels_monitor_task(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Stop irrigation cancels the stored task."""
        mock_client = create_mock_solem_client(2)

        monitor_started = asyncio.Event()

        async def quick_sprinkle(*args, **kwargs):
            pass

        async def slow_monitor(station, duration):
            monitor_started.set()
            await asyncio.sleep(10)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(side_effect=quick_sprinkle)
        mock_client.get_status = AsyncMock(return_value={
            "controller_state": "On",
            "is_watering": True,
            "battery_voltage": 90,
            "battery_level": 5,
            "battery_low": False,
            "station_num": 1,
            "remaining_seconds": 60,
        })

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

            stop_task = asyncio.create_task(coordinator.stop_irrigation())
            await stop_task
            await start_task

            assert coordinator._irrigation_monitor_task is None

    async def test_failed_start_resets_active_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Failed start resets _irrigation_active state."""
        from homeassistant.exceptions import HomeAssistantError

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

            with pytest.raises(HomeAssistantError, match="Failed to start"):
                await coordinator.start_irrigation(1, 1)

            assert coordinator._irrigation_active is False

    async def test_async_shutdown_cancels_monitor_task(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """async_shutdown cancels and awaits monitor task."""
        mock_client = create_mock_solem_client(2)

        async def slow_sprinkle(*args, **kwargs):
            await asyncio.sleep(10)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(side_effect=slow_sprinkle)

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

    async def test_station_state_optimistically_set_before_command(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Station state is set to Sprinkling before the BLE command completes."""
        mock_client = create_mock_solem_client(2)

        async def slow_sprinkle(*args, **kwargs):
            await asyncio.sleep(1)

        mock_client.sprinkle_station_x_for_y_minutes = AsyncMock(side_effect=slow_sprinkle)

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            for station in coordinator.stations:
                station.state = "Stopped"

            start_task = asyncio.create_task(coordinator.start_irrigation(1, 1))
            await asyncio.sleep(0.01)

            assert coordinator.stations[0].state == "Sprinkling"

            await start_task
