"""Coordinator reconfiguration tests."""

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

from conftest import create_mock_solem_client


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
