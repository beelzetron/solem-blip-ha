"""Bluetooth repair issues for the Solem BL-IP integration.

The repair issue is driven by a sliding-window BLE health signal: every
degradation event (failed status poll, release timeout, failed metadata or
irrigation-config read) is timestamped, and when enough events accumulate
within the window the repair issue is created. Intermittent degradation that
a strictly consecutive-failure check would miss therefore still raises the
issue, while isolated transients stay silent. The issue clears only after a
sustained recovery period, so flapping connections keep it visible.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

ISSUE_BLUETOOTH_UNAVAILABLE = "bluetooth_unavailable"
ISSUE_BLUETOOTH_STUCK_ADAPTER = "bluetooth_stuck_adapter"
WINDOW_SECONDS = 30 * 60
WINDOW_FAILURE_THRESHOLD = 4
RECOVERY_CLEAR_SECONDS = 10 * 60
# Historical name kept so tooling and tests referencing the old
# consecutive-failure threshold keep working against the window threshold.
CONSECUTIVE_FAILURES_THRESHOLD = WINDOW_FAILURE_THRESHOLD


def async_create_bluetooth_unavailable_issue(
    coordinator: SolemCoordinator,
) -> None:
    """Create a repair issue when BLE health degrades repeatedly."""
    entry = coordinator.config_entry
    assert entry is not None
    ir.async_create_issue(
        coordinator.hass,
        DOMAIN,
        f"{ISSUE_BLUETOOTH_UNAVAILABLE}_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_BLUETOOTH_UNAVAILABLE,
        translation_placeholders={
            "mac": coordinator.controller_mac_address,
        },
    )


def async_create_bluetooth_stuck_adapter_issue(
    coordinator: SolemCoordinator,
) -> None:
    """Create the stuck-adapter repair issue (Layer B, solem-blip-ble #51).

    Raised once the stuck-recovery signature is complete: a long streak of
    fully degraded poll cycles combined with a cleanup-exhausted warning
    from the library. Unlike the sliding-window issue this one is raised
    once per detection and is not cleared by transient recovery — only a
    config-entry reload or restart clears it, because the adapter state it
    describes survives anything shorter.
    """
    entry = coordinator.config_entry
    assert entry is not None
    ir.async_create_issue(
        coordinator.hass,
        DOMAIN,
        f"{ISSUE_BLUETOOTH_STUCK_ADAPTER}_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_BLUETOOTH_STUCK_ADAPTER,
        translation_placeholders={
            "mac": coordinator.controller_mac_address,
        },
    )


def async_clear_bluetooth_unavailable_issue(coordinator: SolemCoordinator) -> None:
    """Clear the BLE unavailable repair issue after sustained recovery."""
    entry = coordinator.config_entry
    assert entry is not None
    ir.async_delete_issue(
        coordinator.hass,
        DOMAIN,
        f"{ISSUE_BLUETOOTH_UNAVAILABLE}_{entry.entry_id}",
    )


def note_ble_degradation(
    coordinator: SolemCoordinator, *, source: str, now: float | None = None
) -> None:
    """Record a BLE degradation event and raise the issue past the threshold."""
    if now is None:
        now = time.monotonic()
    coordinator._ble_health_events.append(now)
    _prune_window(coordinator, now)
    coordinator._ble_first_healthy_at = None
    if (
        not coordinator._ble_issue_active
        and len(coordinator._ble_health_events) >= WINDOW_FAILURE_THRESHOLD
    ):
        coordinator._ble_issue_active = True
        _LOGGER.warning(
            "%s - BLE health degraded: %d failures in the last %d minutes "
            "(latest: %s). If the problem persists, check the controller's "
            "battery and radio range; rebooting a Bluetooth proxy, or "
            "restarting Home Assistant (which reloads the Bluetooth "
            "adapter), typically resolves it.",
            coordinator.controller_mac_address,
            len(coordinator._ble_health_events),
            WINDOW_SECONDS // 60,
            source,
        )
        async_create_bluetooth_unavailable_issue(coordinator)


def note_ble_recovery(
    coordinator: SolemCoordinator, *, now: float | None = None
) -> None:
    """Record a healthy status poll; clear the issue after sustained recovery."""
    if now is None:
        now = time.monotonic()
    if not coordinator._ble_issue_active:
        return
    if coordinator._ble_first_healthy_at is None:
        coordinator._ble_first_healthy_at = now
        return
    if now - coordinator._ble_first_healthy_at >= RECOVERY_CLEAR_SECONDS:
        coordinator._ble_issue_active = False
        coordinator._ble_first_healthy_at = None
        _LOGGER.info(
            "%s - BLE health recovered; clearing the Bluetooth unavailable "
            "repair issue",
            coordinator.controller_mac_address,
        )
        async_clear_bluetooth_unavailable_issue(coordinator)


def is_device_stale(coordinator: SolemCoordinator) -> bool:
    """Return True when BLE health justifies allowing device removal."""
    return (
        coordinator._ble_issue_active
        or len(coordinator._ble_health_events) >= WINDOW_FAILURE_THRESHOLD
    )


def _prune_window(coordinator: SolemCoordinator, now: float) -> None:
    """Drop degradation events that fell out of the observation window."""
    events = coordinator._ble_health_events
    while events and now - events[0] > WINDOW_SECONDS:
        events.popleft()
