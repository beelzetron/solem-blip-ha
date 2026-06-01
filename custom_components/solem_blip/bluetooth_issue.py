"""Bluetooth repair issues for the Solem BL-IP integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

ISSUE_BLUETOOTH_UNAVAILABLE = "bluetooth_unavailable"
CONSECUTIVE_FAILURES_THRESHOLD = 3


def async_create_bluetooth_unavailable_issue(
    coordinator: SolemCoordinator,
) -> None:
    """Create a repair issue when BLE polling fails repeatedly."""
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


def async_clear_bluetooth_unavailable_issue(coordinator: SolemCoordinator) -> None:
    """Clear the BLE unavailable repair issue after a successful poll."""
    entry = coordinator.config_entry
    assert entry is not None
    ir.async_delete_issue(
        coordinator.hass,
        DOMAIN,
        f"{ISSUE_BLUETOOTH_UNAVAILABLE}_{entry.entry_id}",
    )


def async_manage_bluetooth_issue(
    coordinator: SolemCoordinator, *, success: bool
) -> None:
    """Track consecutive poll failures and manage the repair issue."""
    if success:
        coordinator._consecutive_update_failures = 0
        async_clear_bluetooth_unavailable_issue(coordinator)
        return

    coordinator._consecutive_update_failures = (
        getattr(coordinator, "_consecutive_update_failures", 0) + 1
    )
    if coordinator._consecutive_update_failures >= CONSECUTIVE_FAILURES_THRESHOLD:
        async_create_bluetooth_unavailable_issue(coordinator)
