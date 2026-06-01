"""Coordinator polling and update tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.api import APIConnectionError
from custom_components.solem_blip.const import SOLEM_API_MOCK
from custom_components.solem_blip.coordinator import SolemCoordinator


@pytest.mark.asyncio
async def test_async_init_does_not_block_on_ble_io(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Entity descriptors are built before the first BLE poll."""
    config_entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )
    mock_solem_client.mock = False

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
class TestEntitySetupMetadata:
    """Metadata polling behavior."""

    async def test_device_metadata_is_surfaced(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Firmware and controller-provided station names are surfaced."""
        from homeassistant.helpers import device_registry as dr

        from custom_components.solem_blip.const import DOMAIN

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

    async def test_device_metadata_failures_are_cooled_down(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_solem_client: MagicMock,
    ) -> None:
        """Optional metadata failures do not stall every status refresh."""
        import asyncio

        mock_solem_client.get_firmware_version.side_effect = asyncio.TimeoutError
        mock_solem_client.get_station_names.side_effect = asyncio.TimeoutError

        with patch(
            "custom_components.solem_blip.coordinator.SolemClient",
            return_value=mock_solem_client,
        ), patch(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        ):
            coordinator = SolemCoordinator(hass, mock_config_entry)
            await coordinator.async_init()

            await coordinator._fetch_device_metadata()
            await coordinator._fetch_device_metadata()

            mock_solem_client.get_firmware_version.assert_awaited_once()
            mock_solem_client.get_station_names.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_time_runs_on_first_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Device time sync runs on the first real BLE poll when not mocked."""
    config_entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()

    mock_solem_client.set_time.assert_awaited_once()
    assert coordinator._last_set_time_sync is not None
    assert coordinator._set_time_pending is False


@pytest.mark.asyncio
async def test_set_time_throttled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Device time sync is throttled after the first successful sync."""
    config_entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        data=mock_config_entry.data,
        options={
            **mock_config_entry.options,
            SOLEM_API_MOCK: "false",
        },
        unique_id=mock_config_entry.unique_id,
    )
    mock_solem_client.mock = False

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()
        await coordinator._fetch_device_status()

    mock_solem_client.set_time.assert_awaited_once()


@pytest.mark.asyncio
async def test_irrigation_config_failures_are_cooled_down(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Irrigation config read failures apply a retry cooldown."""
    import asyncio

    mock_solem_client.get_irrigation_config.side_effect = asyncio.TimeoutError

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator.schedule_coordinator.async_refresh()
        await coordinator.schedule_coordinator.async_refresh()

    mock_solem_client.get_irrigation_config.assert_awaited_once()
    assert coordinator.irrigation_programs == {}


@pytest.mark.asyncio
async def test_status_poll_does_not_read_irrigation_config(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Slow schedule reads do not delay the status coordinator."""
    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_status()

    mock_solem_client.get_irrigation_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_irrigation_config_read_is_deferred_while_watering(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Schedule coordinator skips BLE reads during manual irrigation."""
    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator._irrigation_active = True
        await coordinator.schedule_coordinator.async_refresh()

    mock_solem_client.get_irrigation_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_poll_runs_during_manual_irrigation(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Device-initiated program runs still receive status polls during HA manual irrigation."""
    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator._irrigation_active = True
        await coordinator._fetch_device_status()

    mock_solem_client.get_status.assert_awaited_once()
    mock_solem_client.get_station_names.assert_not_awaited()


@pytest.mark.asyncio
async def test_station_names_slow_read_uses_extended_timeout(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Station name reads longer than 5s succeed within STATION_NAMES_READ_TIMEOUT."""
    import asyncio

    async def slow_names() -> dict[int, str]:
        await asyncio.sleep(0.01)
        return {1: "Zone 1", 2: "Zone 2"}

    mock_solem_client.get_station_names = slow_names

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_metadata()

    assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}


@pytest.mark.asyncio
async def test_station_names_partial_reads_merge(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Partial station-name reads accumulate across retries."""
    responses = [{1: "Zone 1"}, {2: "Zone 2"}]

    async def partial_names() -> dict[int, str]:
        return responses.pop(0)

    mock_solem_client.get_station_names = partial_names

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_metadata()
        coordinator._station_names_retry_after = 0.0
        await coordinator._fetch_device_metadata()

    assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}
