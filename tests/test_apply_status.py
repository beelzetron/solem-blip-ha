"""Tests for apply_status coordinator helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.solem_blip.coordinator_polling import apply_status


@pytest.fixture
def coordinator() -> MagicMock:
    """Minimal coordinator mock with station list."""
    coord = MagicMock()
    coord.controller_mac_address = "AA:BB:CC:DD:EE:FF"
    coord.num_stations = 2
    coord.stations = [MagicMock(state="stopped"), MagicMock(state="stopped")]
    coord.controller.state = None
    coord.battery_voltage = None
    coord.battery_level = None
    coord.battery_low = False
    coord.active_program_num = None
    coord.watering_origin = None
    coord.active_station_num = None
    coord.remaining_seconds = None
    coord.controller_off_mode = "unknown"
    coord.controller_off_days_remaining = None
    return coord


def test_apply_status_program_run_sets_program_origin(coordinator: MagicMock) -> None:
    """0x44 program run maps watering_origin to program when active_program is set."""
    apply_status(
        coordinator,
        {
            "controller_state": "On",
            "is_watering": True,
            "station_num": 1,
            "remaining_seconds": 1500,
            "battery_voltage": 79,
            "battery_level": 4,
            "battery_low": False,
            "active_program": 1,
            "watering_origin": "schedule",
        },
    )
    assert coordinator.active_program_num == 1
    assert coordinator.watering_origin == "program"
    assert coordinator.stations[0].state == "sprinkling"


def test_apply_status_clears_program_when_idle(coordinator: MagicMock) -> None:
    apply_status(
        coordinator,
        {
            "controller_state": "On",
            "is_watering": False,
            "station_num": None,
            "remaining_seconds": None,
            "battery_voltage": 79,
            "battery_level": 4,
            "battery_low": False,
            "active_program": None,
            "watering_origin": None,
        },
    )
    assert coordinator.active_program_num is None
    assert coordinator.watering_origin is None


def test_apply_status_stores_controller_off_days(coordinator: MagicMock) -> None:
    apply_status(
        coordinator,
        {
            "controller_state": "Off",
            "controller_off_mode": "temporary",
            "controller_off_days_remaining": 3,
            "is_watering": False,
            "station_num": None,
            "remaining_seconds": None,
            "battery_voltage": 79,
            "battery_level": 4,
            "battery_low": False,
            "active_program": None,
            "watering_origin": None,
        },
    )
    assert coordinator.controller_off_mode == "temporary"
    assert coordinator.controller_off_days_remaining == 3


def test_apply_status_keeps_program_during_inter_station_idle(
    coordinator: MagicMock,
) -> None:
    """Program run stays visible when controller is idle between stations."""
    apply_status(
        coordinator,
        {
            "controller_state": "On",
            "is_watering": False,
            "station_num": None,
            "remaining_seconds": None,
            "battery_voltage": 79,
            "battery_level": 4,
            "battery_low": False,
            "active_program": 1,
            "watering_origin": "program",
        },
    )
    assert coordinator.active_program_num == 1
    assert coordinator.watering_origin == "program"
    assert coordinator.stations[0].state == "stopped"


def test_apply_status_preserves_program_during_watering_without_byte_8(
    coordinator: MagicMock,
) -> None:
    """Keep program identity when a watering frame omits byte 8."""
    coordinator.active_program_num = 2
    coordinator.watering_origin = "program"
    apply_status(
        coordinator,
        {
            "controller_state": "On",
            "is_watering": True,
            "station_num": 1,
            "remaining_seconds": 900,
            "battery_voltage": 79,
            "battery_level": 4,
            "battery_low": False,
            "active_program": None,
            "watering_origin": "manual",
        },
    )
    assert coordinator.active_program_num == 2
    assert coordinator.watering_origin == "program"
    assert coordinator.stations[0].state == "sprinkling"
    assert coordinator.stations[1].state == "stopped"
