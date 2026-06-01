"""Integration setup and unload lifecycle tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import (
    RuntimeData,
    _async_update_listener,
    async_remove_config_entry_device,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.solem_blip.repairs import CONSECUTIVE_FAILURES_THRESHOLD


@pytest.mark.asyncio
async def test_offline_setup_raises_retry_state_and_cleans_up(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A failed first refresh closes BLE and leaves no runtime reference."""
    coordinator = MagicMock()
    coordinator.async_init = AsyncMock()
    coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=ConfigEntryNotReady
    )
    coordinator.async_shutdown = AsyncMock()

    with patch(
        "custom_components.solem_blip.SolemCoordinator",
        return_value=coordinator,
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, mock_config_entry)

    coordinator.async_shutdown.assert_awaited_once()
    assert mock_config_entry.runtime_data is None


@pytest.mark.asyncio
async def test_successful_setup_refreshes_before_platform_forwarding(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Platforms only see the coordinator after its first successful refresh."""
    coordinator = MagicMock()
    coordinator.data = []
    coordinator.async_init = AsyncMock()

    async def first_refresh() -> None:
        coordinator.data = [{"device_id": "controller"}]

    coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=first_refresh)
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch(
        "custom_components.solem_blip.SolemCoordinator",
        return_value=coordinator,
    ):
        assert await async_setup_entry(hass, mock_config_entry)

    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    assert mock_config_entry.runtime_data.coordinator.data


@pytest.mark.asyncio
async def test_unload_returns_false_when_platforms_fail(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unload aborts when platform unloading fails."""
    coordinator = MagicMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator, MagicMock())
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    assert await async_unload_entry(hass, mock_config_entry) is False

    coordinator.async_shutdown.assert_not_called()
    assert mock_config_entry.runtime_data is not None


@pytest.mark.asyncio
async def test_unload_disconnects_and_clears_runtime_reference(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unload closes BLE after platforms unload and clears runtime data."""
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator, MagicMock())
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await async_unload_entry(hass, mock_config_entry)

    coordinator.async_shutdown.assert_awaited_once()
    assert mock_config_entry.runtime_data is None


@pytest.mark.asyncio
async def test_update_listener_reloads_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Option changes trigger a config entry reload."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_reload = AsyncMock()

    await _async_update_listener(hass, mock_config_entry)

    hass.config_entries.async_reload.assert_awaited_once_with(
        mock_config_entry.entry_id
    )


@pytest.mark.asyncio
async def test_remove_entry_requests_rediscovery(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Removing an entry asks HA Bluetooth to rediscover the controller."""
    rediscover = MagicMock()
    mock_bluetooth = MagicMock()
    mock_bluetooth.async_rediscover_address = rediscover

    with patch.dict(
        "sys.modules",
        {"homeassistant.components.bluetooth": mock_bluetooth},
    ):
        await async_remove_entry(hass, mock_config_entry)

    rediscover.assert_called_once_with(hass, "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_remove_config_entry_device_is_rejected_while_healthy(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Device removal is rejected while the controller is updating."""
    device_entry = MagicMock()
    coordinator = MagicMock()
    coordinator._consecutive_update_failures = 0
    mock_config_entry.runtime_data = RuntimeData(coordinator, MagicMock())

    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, device_entry
    )


@pytest.mark.asyncio
async def test_remove_config_entry_device_is_allowed_after_repeated_failures(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Device removal is enabled after the stale-device failure threshold."""
    device_entry = MagicMock()
    coordinator = MagicMock()
    coordinator._consecutive_update_failures = CONSECUTIVE_FAILURES_THRESHOLD
    mock_config_entry.runtime_data = RuntimeData(coordinator, MagicMock())

    assert await async_remove_config_entry_device(hass, mock_config_entry, device_entry)
