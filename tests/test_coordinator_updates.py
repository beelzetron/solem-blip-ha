"""Coordinator update and repair integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.solem_blip.bluetooth_issue import WINDOW_FAILURE_THRESHOLD


@pytest.mark.asyncio
async def test_coordinator_update_failure_raises_update_failed(coordinator) -> None:
    """Failed BLE polling raises UpdateFailed."""
    with patch.object(
        coordinator,
        "async_update_all_sensors",
        new=AsyncMock(side_effect=RuntimeError("offline")),
    ):
        with pytest.raises(UpdateFailed, match="Failed to update BLE status"):
            await coordinator.async_update_data()


@pytest.mark.asyncio
async def test_coordinator_failures_feed_the_ble_health_window(coordinator) -> None:
    """Repeated update failures accumulate as window degradation events."""
    with patch.object(
        coordinator,
        "async_update_all_sensors",
        new=AsyncMock(side_effect=RuntimeError("offline")),
    ), patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        for _ in range(WINDOW_FAILURE_THRESHOLD):
            with pytest.raises(UpdateFailed):
                await coordinator.async_update_data()

        assert len(coordinator._ble_health_events) == WINDOW_FAILURE_THRESHOLD
        assert coordinator._ble_issue_active is True
        create_issue.assert_called_once()
