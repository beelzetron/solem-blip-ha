"""Tests for poll-level BLE cycle health reporting."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from solem_blip_ble.exceptions import SolemConnectionError, SolemDeadlineExceeded

from custom_components.solem_blip.ble_health import note_cycle_outcome
from custom_components.solem_blip.coordinator import SolemCoordinator

BLE_HEALTH_LOGGER = "custom_components.solem_blip.ble_health"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == BLE_HEALTH_LOGGER
    ]


async def test_first_degraded_cycle_logs_warning_with_guidance(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first degraded cycle warns once and carries the recovery guidance."""
    with caplog.at_level(logging.DEBUG):
        note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")

    assert coordinator._ble_cycle_degraded_streak == 1
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "BLE cycle degraded (status poll failed)" in warnings[0]
    assert "usually recovers" in warnings[0]


async def test_consecutive_degraded_cycles_stay_at_one_warning_each(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeat degraded cycles warn once each without repeating the guidance."""
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")

    assert coordinator._ble_cycle_degraded_streak == 3
    warnings = _warnings(caplog)
    assert len(warnings) == 3
    assert "usually recovers" in warnings[0]
    assert "usually recovers" not in warnings[1]
    assert "usually recovers" not in warnings[2]
    assert "3 consecutive degraded cycles" in warnings[2]


async def test_recovery_resets_streak_and_logs_info(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A healthy cycle closes the streak with one recovery line."""
    note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")
    note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")

    with caplog.at_level(logging.DEBUG):
        note_cycle_outcome(coordinator, degraded=False, reason="")
        note_cycle_outcome(coordinator, degraded=False, reason="")

    assert coordinator._ble_cycle_degraded_streak == 0
    recovery = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO
    ]
    assert recovery == ["AA:BB:CC:DD:EE:FF - BLE cycle recovered after 2 degraded cycle(s)"]


async def test_healthy_cycle_without_streak_is_silent(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Healthy cycles on their own never log."""
    with caplog.at_level(logging.DEBUG):
        note_cycle_outcome(coordinator, degraded=False, reason="")

    assert coordinator._ble_cycle_degraded_streak == 0
    assert caplog.records == []


async def test_recovery_requests_device_time_sync(
    coordinator: SolemCoordinator,
) -> None:
    """Recovering from a degraded streak forces a device-time re-sync."""
    coordinator._set_time_pending = False
    note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")
    note_cycle_outcome(coordinator, degraded=False, reason="")
    assert coordinator._set_time_pending is True


async def test_healthy_cycle_leaves_time_sync_pending_untouched(
    coordinator: SolemCoordinator,
) -> None:
    """Healthy cycles without a streak do not force a device-time re-sync."""
    coordinator._set_time_pending = False
    note_cycle_outcome(coordinator, degraded=False, reason="")
    assert coordinator._set_time_pending is False


async def test_metadata_failures_emit_one_warning_and_debug_detail(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both metadata reads failing produce exactly one consolidated warning."""
    mock_solem_client.get_firmware_version.side_effect = asyncio.TimeoutError
    mock_solem_client.get_station_name_snapshot.side_effect = asyncio.TimeoutError

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        ble_coordinator = SolemCoordinator(hass, mock_config_entry)
        await ble_coordinator.async_init()

        with caplog.at_level(logging.DEBUG):
            await ble_coordinator._fetch_device_metadata()

    warnings = _warnings(caplog)
    assert warnings == [
        "AA:BB:CC:DD:EE:FF - BLE cycle degraded "
        "(firmware read and station names read). "
        "The link usually recovers on the next poll. If the problem "
        "persists, check the controller's battery and radio range; "
        "rebooting a Bluetooth proxy, or restarting Home Assistant (which "
        "reloads the Bluetooth adapter), typically resolves it."
    ]
    assert ble_coordinator._ble_cycle_degraded_streak == 1
    debug_details = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.DEBUG
        and "Failed to read" in record.getMessage()
    ]
    assert len(debug_details) == 2


async def test_status_failure_emits_one_warning(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed status poll consolidates into the poll-level warning."""
    mock_solem_client.get_status.side_effect = asyncio.TimeoutError

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        ble_coordinator = SolemCoordinator(hass, mock_config_entry)
        await ble_coordinator.async_init()
        # This legacy test verifies warning behavior outside startup grace.
        ble_coordinator._ble_startup_grace_until = 0.0

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(HomeAssistantError):
                await ble_coordinator.async_update_data()

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "BLE cycle degraded (status poll failed)" in warnings[0]
    assert ble_coordinator._ble_cycle_degraded_streak == 1


async def test_healthy_poll_resets_streak_after_status_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Recovery after a failed poll logs the recovery line and resets."""
    mock_solem_client.get_status.side_effect = asyncio.TimeoutError

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        ble_coordinator = SolemCoordinator(hass, mock_config_entry)
        await ble_coordinator.async_init()

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(HomeAssistantError):
                await ble_coordinator.async_update_data()

            mock_solem_client.get_status.side_effect = None
            await ble_coordinator.async_update_data()

    assert ble_coordinator._ble_cycle_degraded_streak == 0
    recovery = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO
        and record.name == BLE_HEALTH_LOGGER
    ]
    assert recovery == ["AA:BB:CC:DD:EE:FF - BLE cycle recovered after 1 degraded cycle(s)"]


async def test_ble_startup_grace_quiets_transient_connection_errors(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Startup grace affects log severity, never health counters."""
    with caplog.at_level(logging.DEBUG):
        note_cycle_outcome(
            coordinator,
            degraded=True,
            reason="status poll failed",
            error=TimeoutError("BLE timeout"),
        )
    assert _warnings(caplog) == []
    assert coordinator._ble_cycle_degraded_streak == 1
    assert any(
        record.levelno == logging.DEBUG
        and "BLE cycle degraded" in record.getMessage()
        for record in caplog.records
    )


async def test_ble_grace_does_not_hide_unrelated_failures(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-connection failures retain warning severity during grace."""
    note_cycle_outcome(
        coordinator,
        degraded=True,
        reason="status poll failed",
        error=ValueError("invalid status payload"),
    )
    assert len(_warnings(caplog)) == 1


async def test_ble_maintenance_is_bounded_and_clears_on_recovery(
    coordinator: SolemCoordinator,
) -> None:
    """Repeated calls cannot extend a running maintenance window."""
    coordinator._ble_startup_grace_until = 0.0
    coordinator.start_ble_maintenance(120)
    first_deadline = coordinator._ble_maintenance_until
    assert coordinator.ble_grace_active()
    coordinator.start_ble_maintenance(600)
    assert coordinator._ble_maintenance_until == first_deadline
    coordinator.clear_ble_maintenance()
    assert not coordinator.ble_grace_active()


async def test_ble_grace_expires(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Expired grace restores warnings for BLE connection failures."""
    coordinator._ble_startup_grace_until = 0.0
    coordinator._ble_maintenance_until = 0.0
    note_cycle_outcome(
        coordinator,
        degraded=True,
        reason="status poll failed",
        error=ConnectionError("proxy disconnected"),
    )
    assert len(_warnings(caplog)) == 1


@pytest.mark.parametrize(
    "error",
    [
        SolemConnectionError("BLE connection lost"),
        SolemDeadlineExceeded("BLE operation deadline"),
    ],
)
async def test_solem_transport_errors_are_quiet_during_grace(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Solem BLE library transport exceptions are transient during grace."""
    with caplog.at_level(logging.DEBUG):
        note_cycle_outcome(
            coordinator, degraded=True, reason="status poll failed", error=error
        )
    assert _warnings(caplog) == []
    assert coordinator._ble_cycle_degraded_streak == 1


async def test_wrapped_ble_transport_error_is_quiet_during_grace(
    coordinator: SolemCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An application-level wrapper retains the BLE cause classification."""
    try:
        raise SolemConnectionError("BLE link dropped")
    except SolemConnectionError as cause:
        wrapped = RuntimeError("status poll failed")
        wrapped.__cause__ = cause

    with caplog.at_level(logging.DEBUG):
        note_cycle_outcome(
            coordinator, degraded=True, reason="status poll failed", error=wrapped
        )
    assert _warnings(caplog) == []
    assert coordinator._ble_cycle_degraded_streak == 1
