"""Poll-level BLE cycle health reporting for the Solem BL-IP integration.

A single degraded BLE/proxy cycle can previously emit several warnings
(release timeout, firmware read, station names, irrigation config, status
poll). This module consolidates them into one poll-level warning per
degraded cycle, with the individual read failures logged at DEBUG level.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .bluetooth_issue import note_ble_degradation

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

_RECOVERY_HINT = (
    "The link usually recovers on the next poll. If the problem persists, "
    "check the controller's battery and radio range; rebooting a Bluetooth "
    "proxy, or restarting Home Assistant (which reloads the Bluetooth "
    "adapter), typically resolves it."
)


def note_cycle_outcome(
    coordinator: SolemCoordinator, *, degraded: bool, reason: str
) -> None:
    """Record the poll cycle outcome and log one warning per degraded cycle.

    The first degraded cycle of a streak carries the recovery guidance;
    consecutive degraded cycles keep the streak count so repeated noise
    stays at one line per poll instead of one line per failed read.
    """
    streak = coordinator._ble_cycle_degraded_streak

    if not degraded:
        if streak:
            _LOGGER.info(
                "%s - BLE cycle recovered after %d degraded cycle(s)",
                coordinator.controller_mac_address,
                streak,
            )
        coordinator._ble_cycle_degraded_streak = 0
        return

    streak += 1
    coordinator._ble_cycle_degraded_streak = streak
    note_ble_degradation(coordinator, source=reason)
    if streak == 1:
        _LOGGER.warning(
            "%s - BLE cycle degraded (%s). %s",
            coordinator.controller_mac_address,
            reason,
            _RECOVERY_HINT,
        )
    else:
        _LOGGER.warning(
            "%s - BLE cycle degraded (%s), %d consecutive degraded cycles",
            coordinator.controller_mac_address,
            reason,
            streak,
        )
