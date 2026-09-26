"""Tests for stuck-recovery detection (Layer B, solem-blip-ble #51 part 2)."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from custom_components.solem_blip.ble_health import note_cycle_outcome
from custom_components.solem_blip.bluetooth_issue import (
    ISSUE_BLUETOOTH_STUCK_ADAPTER,
)
from custom_components.solem_blip.stuck_recovery import (
    CLEANUP_EXHAUSTED_SIGNATURE,
    DEGRADED_CYCLE_STUCK_THRESHOLD,
    StuckAdapterDetector,
    attach_stuck_adapter_detector,
    detach_stuck_adapter_detector,
)

STUCK_LOGGER = "solem_blip_ble.client_persistent"
STUCK_RECOVERY_LOGGER = "custom_components.solem_blip.stuck_recovery"


def _emit_cleanup_exhausted() -> None:
    logging.getLogger(STUCK_LOGGER).warning(
        "AA:BB:CC:DD:EE:FF - %s", CLEANUP_EXHAUSTED_SIGNATURE
    )


def _stuck_calls(create_issue) -> list:
    """Filter mocked issue-registry calls down to stuck-adapter issue ids."""
    return [
        call
        for call in create_issue.call_args_list
        if call.args and str(call.args[2]).startswith(ISSUE_BLUETOOTH_STUCK_ADAPTER)
    ]


@pytest.fixture(autouse=True)
def clean_root_observer():
    """Ensure each test starts and ends without the shared observer handler."""
    import custom_components.solem_blip.stuck_recovery as sr

    root = logging.getLogger()

    def _remove_observer() -> None:
        for existing in list(root.handlers):
            if type(existing).__name__ == "_CleanupExhaustedObserver":
                root.removeHandler(existing)
        sr._CLEANUP_FILTER_OWNER = None

    _remove_observer()
    yield
    _remove_observer()


async def test_threshold_requires_both_signals(coordinator) -> None:
    """A long degraded streak alone does not raise the stuck-adapter issue."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        for _ in range(DEGRADED_CYCLE_STUCK_THRESHOLD + 2):
            note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")
            coordinator.stuck_adapter_detector.note_cycle_outcome(degraded=True)

    assert _stuck_calls(create_issue) == []
    assert coordinator.stuck_adapter_detector.issue_raised is False


async def test_cleanup_warning_alone_does_not_raise_issue(coordinator) -> None:
    """A cleanup-exhausted warning without a degraded streak stays silent."""
    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        detector = coordinator.stuck_adapter_detector
        detector.note_cleanup_exhausted()
        detector.note_cycle_outcome(degraded=True)

    create_issue.assert_not_called()


async def test_stuck_signature_raises_repair_issue(
    coordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """Streak >= 10 plus a cleanup-exhausted warning raises the repair issue."""
    _emit_cleanup_exhausted()

    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        for _ in range(DEGRADED_CYCLE_STUCK_THRESHOLD):
            note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")
            coordinator.stuck_adapter_detector.note_cycle_outcome(degraded=True)

    stuck_calls = _stuck_calls(create_issue)
    assert len(stuck_calls) == 1
    assert coordinator.stuck_adapter_detector.issue_raised is True

    escalation = [
        record.getMessage()
        for record in caplog.records
        if record.name == STUCK_RECOVERY_LOGGER
        and record.levelno == logging.WARNING
    ]
    assert len(escalation) == 1
    assert "The Bluetooth adapter may be stuck" in escalation[0]
    assert "restart the host" in escalation[0]
    assert "not just Home Assistant core" in escalation[0]


async def test_issue_is_raised_only_once(coordinator) -> None:
    """Further degraded cycles after detection do not re-raise the issue."""
    _emit_cleanup_exhausted()

    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        for _ in range(DEGRADED_CYCLE_STUCK_THRESHOLD + 5):
            note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")
            coordinator.stuck_adapter_detector.note_cycle_outcome(degraded=True)

    assert len(_stuck_calls(create_issue)) == 1


async def test_healthy_cycle_does_not_trigger_detection(coordinator) -> None:
    """Healthy cycles never feed the stuck-adapter check."""
    _emit_cleanup_exhausted()
    detector = coordinator.stuck_adapter_detector

    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        for _ in range(DEGRADED_CYCLE_STUCK_THRESHOLD + 2):
            detector.note_cycle_outcome(degraded=False)

    create_issue.assert_not_called()


async def test_wedge_and_recover_cycle_never_trips(coordinator) -> None:
    """Historical episode shape (degraded, recover, repeat) never fires."""
    _emit_cleanup_exhausted()
    detector = coordinator.stuck_adapter_detector

    with patch(
        "custom_components.solem_blip.bluetooth_issue.ir.async_create_issue"
    ) as create_issue:
        # Historical episode shape (degraded, recover, repeat): the longest
        # recorded wedge-and-recover episode self-healed inside ~10 minutes
        # (at a >= 90 s poll interval that is fewer than 10 consecutive
        # degraded cycles).
        for _ in range(DEGRADED_CYCLE_STUCK_THRESHOLD - 1):
            detector.note_cycle_outcome(degraded=True)
        detector.note_cycle_outcome(degraded=False)
        for _ in range(DEGRADED_CYCLE_STUCK_THRESHOLD - 1):
            detector.note_cycle_outcome(degraded=True)

    assert _stuck_calls(create_issue) == []


async def test_root_observer_sees_library_warning(
    coordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """The shared root-logger observer flags real library log records."""
    detector = coordinator.stuck_adapter_detector
    assert detector.cleanup_exhausted_seen is False

    with caplog.at_level(logging.WARNING):
        _emit_cleanup_exhausted()
        logging.getLogger(STUCK_LOGGER).warning("unrelated library warning")

    assert detector.cleanup_exhausted_seen is True
    # The observer must not swallow records: both warnings stay visible.
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == STUCK_LOGGER
    ]
    assert len(messages) == 2


async def test_root_observer_ignores_other_loggers_and_levels(coordinator) -> None:
    """Warnings with the same text from other loggers or levels do not count."""
    detector = coordinator.stuck_adapter_detector

    logging.getLogger("some.other.integration").warning(CLEANUP_EXHAUSTED_SIGNATURE)
    logging.getLogger(STUCK_LOGGER).info(CLEANUP_EXHAUSTED_SIGNATURE)
    assert detector.cleanup_exhausted_seen is False


def test_attach_registers_and_detach_unregisters_handler() -> None:
    """The shared observer handler is installed once and removed when last."""
    import custom_components.solem_blip.stuck_recovery as sr

    detector_a = attach_stuck_adapter_detector(coordinator=None)  # type: ignore[arg-type]
    root = logging.getLogger()
    handlers = [
        existing
        for existing in root.handlers
        if type(existing).__name__ == "_CleanupExhaustedObserver"
    ]
    assert len(handlers) == 1

    detector_b = attach_stuck_adapter_detector(coordinator=None)  # type: ignore[arg-type]
    assert (
        len(
            [
                existing
                for existing in root.handlers
                if type(existing).__name__ == "_CleanupExhaustedObserver"
            ]
        )
        == 1
    )

    detach_stuck_adapter_detector(detector_a)
    assert handlers[0] in root.handlers

    detach_stuck_adapter_detector(detector_b)
    assert not any(
        type(existing).__name__ == "_CleanupExhaustedObserver"
        for existing in root.handlers
    )
    assert sr._CLEANUP_FILTER_OWNER is None


def test_detach_tolerates_unknown_detector() -> None:
    """Detaching a never-registered detector is a no-op."""
    detach_stuck_adapter_detector(
        StuckAdapterDetector(coordinator=None)  # type: ignore[arg-type]
    )


async def test_coordinator_shutdown_detaches_detector(
    hass, mock_config_entry, mock_solem_client
) -> None:
    """Unloading the coordinator removes its detector from the observer."""
    import custom_components.solem_blip.stuck_recovery as sr

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        from custom_components.solem_blip.coordinator import SolemCoordinator

        ble_coordinator = SolemCoordinator(hass, mock_config_entry)
        await ble_coordinator.async_init()
        assert sr._CLEANUP_FILTER_OWNER is not None
        assert ble_coordinator.stuck_adapter_detector in sr._CLEANUP_FILTER_OWNER.detectors

        await ble_coordinator.async_shutdown()

    assert sr._CLEANUP_FILTER_OWNER is None
