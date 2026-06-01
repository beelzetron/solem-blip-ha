"""Coordinator update and repair integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.solem_blip.repairs import CONSECUTIVE_FAILURES_THRESHOLD


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
async def test_coordinator_tracks_consecutive_failures(coordinator) -> None:
    """Repeated update failures increment the repair issue counter."""
    with patch.object(
        coordinator,
        "async_update_all_sensors",
        new=AsyncMock(side_effect=RuntimeError("offline")),
    ), patch(
        "custom_components.solem_blip.repairs.ir.async_create_issue"
    ) as create_issue:
        for _ in range(CONSECUTIVE_FAILURES_THRESHOLD):
            with pytest.raises(UpdateFailed):
                await coordinator.async_update_data()

        assert coordinator._consecutive_update_failures == CONSECUTIVE_FAILURES_THRESHOLD
        create_issue.assert_called_once()
