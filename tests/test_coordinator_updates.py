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


@pytest.mark.asyncio
async def test_planned_maintenance_transient_failure_reuses_idle_snapshot(
    coordinator,
) -> None:
    """Expected proxy downtime is quiet without losing BLE health tracking."""
    from solem_blip_ble.exceptions import SolemConnectionError

    coordinator._ble_startup_grace_until = 0.0
    coordinator.data = [{"last": "known"}]
    coordinator._last_successful_poll_at = 1.0
    coordinator.start_ble_maintenance(120)

    with patch.object(
        coordinator,
        "async_update_all_sensors",
        new=AsyncMock(
            side_effect=SolemConnectionError("Device not found! Failed connecting!")
        ),
    ):
        assert await coordinator.async_update_data() == [{"last": "known"}]

    assert coordinator._ble_cycle_degraded_streak == 1
    assert coordinator._ble_maintenance_suppressed == 1
    assert len(coordinator._ble_health_events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["outside_grace", "unexpected", "watering", "no_snapshot"]
)
async def test_maintenance_does_not_mask_other_failures(coordinator, reason) -> None:
    """Only transient transport errors during safe, explicit maintenance are quiet."""
    from solem_blip_ble.exceptions import SolemConnectionError

    coordinator._ble_startup_grace_until = 0.0
    coordinator.data = [{"last": "known"}]
    coordinator._last_successful_poll_at = 1.0
    coordinator.start_ble_maintenance(120)
    error = SolemConnectionError("proxy disconnected")

    if reason == "outside_grace":
        coordinator._ble_maintenance_until = 0.0
    elif reason == "unexpected":
        error = ValueError("invalid status payload")
    elif reason == "watering":
        coordinator._is_watering = True
    elif reason == "no_snapshot":
        coordinator._last_successful_poll_at = None

    with (
        patch.object(
            coordinator, "async_update_all_sensors", new=AsyncMock(side_effect=error)
        ),
        pytest.raises(UpdateFailed, match="Failed to update BLE status"),
    ):
        await coordinator.async_update_data()
