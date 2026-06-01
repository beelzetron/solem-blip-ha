"""Additional irrigation error-path coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.api import APIConnectionError
from custom_components.solem_blip.coordinator import SolemCoordinator
from custom_components.solem_blip.coordinator_irrigation import (
    monitor_irrigation_until_complete,
    stop_irrigation,
    turn_controller_off,
    turn_controller_on,
)
from tests.conftest import create_mock_solem_client


@pytest.mark.asyncio
async def test_stop_irrigation_connection_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Stop irrigation surfaces translated connection failures."""
    mock_client = create_mock_solem_client(2)
    mock_client.stop_manual_sprinkle = AsyncMock(
        side_effect=APIConnectionError("offline")
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
            await stop_irrigation(coordinator)
        assert exc_info.value.translation_key == "stop_irrigation_failed"


@pytest.mark.asyncio
async def test_turn_controller_on_connection_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Turn-on surfaces translated connection failures."""
    mock_client = create_mock_solem_client(2)
    mock_client.turn_on = AsyncMock(side_effect=APIConnectionError("offline"))

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()

        with pytest.raises(HomeAssistantError) as exc_info:
            await turn_controller_on(coordinator)
        assert exc_info.value.translation_key == "controller_on_failed"


@pytest.mark.asyncio
async def test_turn_controller_off_connection_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Turn-off surfaces translated connection failures."""
    mock_client = create_mock_solem_client(2)
    mock_client.turn_off_permanent = AsyncMock(
        side_effect=APIConnectionError("offline")
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
            await turn_controller_off(coordinator)
        assert exc_info.value.translation_key == "controller_off_failed"


@pytest.mark.asyncio
async def test_monitor_irrigation_handles_status_poll_errors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Monitor loop continues when a status poll fails during watering."""
    mock_client = create_mock_solem_client(2)
    mock_client.get_status = AsyncMock(side_effect=APIConnectionError("offline"))

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ), patch(
        "custom_components.solem_blip.coordinator_irrigation.sleep",
        new=AsyncMock(),
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        coordinator.irrigation_stop_event.set()

        await monitor_irrigation_until_complete(coordinator, station=1, duration=1)

        assert coordinator._irrigation_active is False


def test_irrigation_device_update_state() -> None:
    """Irrigation device models update state in memory."""
    from custom_components.solem_blip.models import IrrigationController

    controller = IrrigationController("id", "name", "uid", None)
    controller.update_state("On")
    assert controller.state == "On"
