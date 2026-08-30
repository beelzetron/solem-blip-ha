"""Repair issue tests for the window-based BLE health guard."""

from __future__ import annotations

from collections import deque
from unittest.mock import patch

import pytest

from custom_components.solem_blip.bluetooth_issue import (
    RECOVERY_CLEAR_SECONDS,
    WINDOW_FAILURE_THRESHOLD,
    WINDOW_SECONDS,
    note_ble_degradation,
    note_ble_recovery,
)


@pytest.mark.asyncio
async def test_repair_issue_created_after_failures_within_window(coordinator) -> None:
    """Repair issue is created once enough failures accumulate in the window."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue, patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_delete_issue"
    ) as delete_issue:
        for attempt in range(WINDOW_FAILURE_THRESHOLD - 1):
            note_ble_degradation(
                coordinator,
                source="status poll failed",
                now=attempt * 60.0,
            )

        create_issue.assert_not_called()

        note_ble_degradation(
            coordinator, source="status poll failed", now=180.0
        )

        create_issue.assert_called_once()
        delete_issue.assert_not_called()
        assert coordinator._ble_issue_active is True


@pytest.mark.asyncio
async def test_intermittent_failures_with_recent_cluster_raise_issue(coordinator) -> None:
    """Sporadic failures plus a recent cluster raise the issue despite gaps."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        # Two sporadic events, then a cluster of four within the window —
        # the shape of the reported intermittent wedge.
        for when in (0.0, 600.0, 6900.0, 7000.0, 7100.0, 7200.0):
            note_ble_degradation(
                coordinator,
                source="status poll failed",
                now=when,
            )

        create_issue.assert_called_once()
        assert len(coordinator._ble_health_events) == WINDOW_FAILURE_THRESHOLD


@pytest.mark.asyncio
async def test_old_failures_expire_out_of_window(coordinator) -> None:
    """Failures older than the window no longer count toward the threshold."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        # Seed the window directly with four stale events.
        coordinator._ble_health_events.extend(
            [0.0, 60.0, 120.0, 180.0]
        )

        # A fresh event an hour later prunes the stale ones out.
        note_ble_degradation(
            coordinator,
            source="status poll failed",
            now=WINDOW_SECONDS + 600.0,
        )

        create_issue.assert_not_called()
        assert list(coordinator._ble_health_events) == [WINDOW_SECONDS + 600.0]


@pytest.mark.asyncio
async def test_issue_clears_only_after_sustained_recovery(coordinator) -> None:
    """The issue persists through brief recovery and clears after the period."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue, patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_delete_issue"
    ) as delete_issue:
        for attempt in range(WINDOW_FAILURE_THRESHOLD):
            note_ble_degradation(
                coordinator,
                source="status poll failed",
                now=attempt * 60.0,
            )

        # Brief recovery attempts do not clear the issue.
        note_ble_recovery(coordinator, now=300.0)
        note_ble_recovery(coordinator, now=600.0)
        delete_issue.assert_not_called()

        # Sustained recovery clears it.
        note_ble_recovery(coordinator, now=300.0 + RECOVERY_CLEAR_SECONDS)

        delete_issue.assert_called_once()
        assert coordinator._ble_issue_active is False
        assert coordinator._ble_first_healthy_at is None

    create_issue.assert_called_once()


@pytest.mark.asyncio
async def test_recovery_is_ignored_when_issue_never_activated(coordinator) -> None:
    """Healthy cycles are no-ops while the issue has never been active."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_delete_issue"
    ) as delete_issue:
        note_ble_recovery(coordinator, now=0.0)
        note_ble_recovery(coordinator, now=RECOVERY_CLEAR_SECONDS + 1.0)

    delete_issue.assert_not_called()
    assert coordinator._ble_first_healthy_at is None


@pytest.mark.asyncio
async def test_degradation_during_recovery_suppresses_clear(coordinator) -> None:
    """A new failure while recovering cancels the pending clear."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_delete_issue"
    ) as delete_issue:
        for attempt in range(WINDOW_FAILURE_THRESHOLD):
            note_ble_degradation(
                coordinator, source="status poll failed", now=attempt * 60.0
            )

        note_ble_recovery(coordinator, now=300.0)
        note_ble_degradation(coordinator, source="release timeout", now=400.0)
        note_ble_recovery(coordinator, now=500.0)
        # Recovery restarts from the post-degradation cycle; 590 s later the
        # sustained-recovery period has not elapsed yet.
        note_ble_recovery(coordinator, now=500.0 + RECOVERY_CLEAR_SECONDS - 10)

    delete_issue.assert_not_called()
    assert coordinator._ble_issue_active is True
    assert coordinator._ble_first_healthy_at is not None


def test_is_device_stale_reflects_window_state(coordinator) -> None:
    """Device removal is allowed once the window shows degraded health."""
    from custom_components.solem_blip.bluetooth_issue import is_device_stale

    assert is_device_stale(coordinator) is False

    for attempt in range(WINDOW_FAILURE_THRESHOLD):
        note_ble_degradation(
            coordinator, source="status poll failed", now=attempt * 60.0
        )

    assert is_device_stale(coordinator) is True

    # Long healthy period: window drained and the issue deactivated again.
    coordinator._ble_health_events = deque()
    coordinator._ble_issue_active = False
    assert is_device_stale(coordinator) is False

    coordinator._ble_issue_active = True
    assert is_device_stale(coordinator) is True
