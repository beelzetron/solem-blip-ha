"""Stuck-recovery detection for the Solem BL-IP integration.

The library logs ``cleanup retry exhausted for stale connection; adapter may
need recovery`` when a resisting disconnect stays unreleased across its
bounded retries. Combined with a long streak of fully degraded poll cycles,
that signature indicates a wedged Bluetooth adapter (HCI-level), which no
in-process retry or session reset can clear — the live-proven remedy is a
host reboot, and a core-only restart is known to be insufficient because the
adapter state survives it.

Layer B from solem-blip-ble #51, part 2: recognize the signature and surface
it through the existing repair-issue machinery with adapter-agnostic
guidance. Ordinary wedge-and-recover episodes self-heal within roughly ten
minutes, well below the threshold, so they never trip the repair.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .bluetooth_issue import async_create_bluetooth_stuck_adapter_issue

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

# The exact warning text the library emits after its bounded cleanup retries
# give up on a stale connection (solem-blip-ble #51, Layer A hook). The
# library prefixes every line with the device MAC, so the signature is
# matched by containment.
CLEANUP_EXHAUSTED_SIGNATURE = (
    "cleanup retry exhausted for stale connection; adapter may need recovery"
)

# A streak this long at the default 90 s scan interval represents roughly
# fifteen minutes of uninterrupted failure. Historical wedge-and-recover
# episodes self-healed in under ten minutes, so they never reach this count.
DEGRADED_CYCLE_STUCK_THRESHOLD = 10

# Cleanup-exhausted warnings are keyed by logger name prefix instead of the
# exact module path so a library-side rename of the emitting module keeps the
# signature working.
_CLEANUP_EXHAUSTED_LOGGERS = ("solem_blip_ble.",)


def _is_cleanup_exhausted_record(record: logging.LogRecord) -> bool:
    """Return True when a record is a cleanup-exhausted library warning."""
    return (
        record.levelno >= logging.WARNING
        and CLEANUP_EXHAUSTED_SIGNATURE in record.getMessage()
        and record.name.startswith(_CLEANUP_EXHAUSTED_LOGGERS)
    )


class _CleanupExhaustedObserver(logging.Handler):
    """Root-logger observer for cleanup-exhausted library warnings.

    A root *filter* only sees records emitted through the root logger
    itself, while library warnings propagate up to root *handlers* — hence
    the observation lives in a handler. The handler has no output stream and
    never suppresses anything downstream, so library logging behavior is
    untouched.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if _is_cleanup_exhausted_record(record):
            owner = _CLEANUP_FILTER_OWNER
            if owner is not None:
                for detector in list(owner.detectors):
                    detector.note_cleanup_exhausted()


class _CleanupObserverOwner:
    """Bookkeeping for the shared root-logger cleanup-exhausted observer."""

    def __init__(
        self, handler: logging.Handler, detectors: list[StuckAdapterDetector]
    ) -> None:
        self.handler = handler
        self.detectors = detectors


_CLEANUP_FILTER_OWNER: _CleanupObserverOwner | None = None


class StuckAdapterDetector:
    """Track BLE signals that indicate a wedged Bluetooth adapter.

    One detector per coordinator. Cycle outcomes come from the coordinator's
    poll path; cleanup-exhausted warnings are observed from the library's
    log stream by the shared root-logger handler this detector registers
    with.
    """

    def __init__(self, coordinator: SolemCoordinator) -> None:
        self._coordinator = coordinator
        self._cleanup_exhausted_seen = False
        self._issue_raised = False

    @property
    def cleanup_exhausted_seen(self) -> bool:
        """Return whether a library cleanup-exhausted warning was observed."""
        return self._cleanup_exhausted_seen

    @property
    def issue_raised(self) -> bool:
        """Return whether the stuck-adapter repair issue was already raised."""
        return self._issue_raised

    def note_cleanup_exhausted(self) -> None:
        """Record a cleanup-exhausted warning from the library."""
        self._cleanup_exhausted_seen = True

    def note_cycle_outcome(self, *, degraded: bool) -> None:
        """Feed one poll-cycle outcome into the stuck-adapter check."""
        if not degraded:
            return
        self._evaluate()

    def _evaluate(self) -> None:
        """Raise the repair issue once the stuck signature is complete."""
        if self._issue_raised:
            return
        coordinator = self._coordinator
        if coordinator._ble_cycle_degraded_streak < DEGRADED_CYCLE_STUCK_THRESHOLD:
            return
        if not self._cleanup_exhausted_seen:
            return
        self._issue_raised = True
        _LOGGER.warning(
            "%s - BLE stuck-recovery signature detected: %d consecutive "
            "degraded cycles with a cleanup-exhausted warning from the BLE "
            "library. The Bluetooth adapter may be stuck. If the problem "
            "persists across a few more polls, restart the host (Settings -> "
            "System -> Restart), not just Home Assistant core.",
            coordinator.controller_mac_address,
            coordinator._ble_cycle_degraded_streak,
        )
        async_create_bluetooth_stuck_adapter_issue(coordinator)


def attach_stuck_adapter_detector(coordinator: SolemCoordinator) -> StuckAdapterDetector:
    """Create the coordinator's stuck-adapter detector and start observing.

    Registers the detector with the shared root-logger handler that watches
    for cleanup-exhausted warnings from the BLE library — the only way to
    observe library log lines without touching library code.
    """
    detector = StuckAdapterDetector(coordinator)
    _register_cleanup_exhausted_detector(detector)
    return detector


def detach_stuck_adapter_detector(detector: StuckAdapterDetector) -> None:
    """Stop observing for a detector whose coordinator is shutting down."""
    _unregister_cleanup_exhausted_detector(detector)


def _register_cleanup_exhausted_detector(detector: StuckAdapterDetector) -> None:
    """Install the observer once and add the detector to its broadcast list."""
    global _CLEANUP_FILTER_OWNER
    if _CLEANUP_FILTER_OWNER is None:
        handler = _CleanupExhaustedObserver()
        logging.getLogger().addHandler(handler)
        _CLEANUP_FILTER_OWNER = _CleanupObserverOwner(handler, [])
    if detector not in _CLEANUP_FILTER_OWNER.detectors:
        _CLEANUP_FILTER_OWNER.detectors.append(detector)


def _unregister_cleanup_exhausted_detector(detector: StuckAdapterDetector) -> None:
    """Remove a detector from the observer; uninstall when the last leaves."""
    global _CLEANUP_FILTER_OWNER
    owner = _CLEANUP_FILTER_OWNER
    if owner is None:
        return
    if detector in owner.detectors:
        owner.detectors.remove(detector)
    if not owner.detectors:
        logging.getLogger().removeHandler(owner.handler)
        _CLEANUP_FILTER_OWNER = None
