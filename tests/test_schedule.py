"""Unit tests for schedule helper functions."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.solem_blip.schedule import (
    build_schedule_attributes,
    enabled_start_count,
    format_start_time,
    next_start_datetime,
    weekday_allowed,
)

CAPTURE_PROGRAM_A = {
    "name": "Programma A",
    "inter_station_delay": 0,
    "water_budget": 100,
    "cycle": 4,
    "week_days": 0x7F,
    "period_length": 2,
    "start_times": [1060, None, None, None, None, None, None, None],
    "station_durations": [1200, 0, 0, 0, 1800, 0],
}

CAPTURE_PROGRAM_B = {
    "name": "Programma B",
    "inter_station_delay": 0,
    "water_budget": 100,
    "cycle": 4,
    "week_days": 0x7F,
    "period_length": 2,
    "start_times": [None] * 8,
    "station_durations": [0] * 6,
}

CAPTURE_PROGRAM_C = {
    "name": "Programma C",
    "inter_station_delay": 0,
    "water_budget": 100,
    "cycle": 4,
    "week_days": 0x11,
    "period_length": 3,
    "start_times": [270, None, None, None, None, None, None, None],
    "station_durations": [0, 1500, 1500, 1500, 0, 0],
}


def test_format_start_time():
    assert format_start_time(1060) == "17:40"
    assert format_start_time(270) == "04:30"
    assert format_start_time(None) == "disabled"


def test_enabled_start_count():
    assert enabled_start_count(CAPTURE_PROGRAM_A["start_times"]) == 1
    assert enabled_start_count(CAPTURE_PROGRAM_B["start_times"]) == 0


def test_weekday_allowed_monday_bit():
    monday = datetime(2026, 6, 1, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    assert weekday_allowed(0x01, monday)


def test_next_start_datetime_later_today():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 6, 1, 10, 0, tzinfo=tz)
    program = {
        **CAPTURE_PROGRAM_A,
        "cycle": 0,
        "week_days": 0x7F,
        "period_length": 0,
    }
    nxt = next_start_datetime(program, now)
    assert nxt == datetime(2026, 6, 1, 17, 40, tzinfo=tz)


def test_next_start_datetime_tomorrow_after_last_slot():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 6, 1, 18, 0, tzinfo=tz)
    program = {
        **CAPTURE_PROGRAM_A,
        "cycle": 0,
        "week_days": 0x7F,
        "period_length": 0,
    }
    nxt = next_start_datetime(program, now)
    assert nxt == datetime(2026, 6, 2, 17, 40, tzinfo=tz)


def test_next_start_datetime_none_when_disabled():
    now = datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("Europe/Rome"))
    assert next_start_datetime(CAPTURE_PROGRAM_B, now) is None


def test_build_schedule_attributes_maps_station_names():
    attrs = build_schedule_attributes(
        CAPTURE_PROGRAM_C,
        {2: "Back", 3: "Side", 4: "Front"},
    )
    assert attrs["start_time_1"] == "04:30"
    assert attrs["next_start_approximate"] is True
    assert attrs["Back_duration_minutes"] == 1500
    assert attrs["Side_duration_minutes"] == 1500
