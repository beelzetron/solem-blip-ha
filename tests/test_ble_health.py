"""Tests for poll-level BLE cycle health reporting."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    """The first degraded cycle warns once and carries the proxy guidance."""
    with caplog.at_level(logging.DEBUG):
        note_cycle_outcome(coordinator, degraded=True, reason="status poll failed")

    assert coordinator._ble_cycle_degraded_streak == 1
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "BLE cycle degraded (status poll failed)" in warnings[0]
    assert "stale session" in warnings[0]


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
    assert "stale session" in warnings[0]
    assert "stale session" not in warnings[1]
    assert "stale session" not in warnings[2]
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


async def test_metadata_failures_emit_one_warning_and_debug_detail(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both metadata reads failing produce exactly one consolidated warning."""
    mock_solem_client.get_firmware_version.side_effect = asyncio.TimeoutError
    mock_solem_client.get_station_names.side_effect = asyncio.TimeoutError

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
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
        "If this controller is connected through an ESPHome Bluetooth proxy, "
        "the proxy may be holding a stale session (for example after a Home "
        "Assistant restart); rebooting the proxy typically resolves this."
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
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        ble_coordinator = SolemCoordinator(hass, mock_config_entry)
        await ble_coordinator.async_init()

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
        "custom_components.solem_blip.coordinator.SolemClient",
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
