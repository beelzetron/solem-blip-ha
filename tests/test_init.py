"""Integration setup and unload lifecycle tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import (
    RuntimeData,
    async_setup_entry,
    async_unload_entry,
)


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
