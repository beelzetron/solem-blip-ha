"""Unit tests for schedule helper functions."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from custom_components.solem_blip.schedule import (
    build_schedule_attributes,
    day_matches_cycle,
    duration_minutes,
    enabled_start_count,
    format_duration,
    format_start_time,
    next_start_datetime,
    periodic_start_day_matches,
    schedule_context_attributes,
    schedule_summary,
    weekday_allowed,
)

CAPTURE_PROGRAM_A = {
    "name": "Programma A",
    "inter_station_delay": 0,
    "water_budget": 100,
    "cycle": 4,
    "week_days": 0x7F,
    "period_length": 2,
    "synchro_day": 0,
    "period_start_date": None,
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
    "synchro_day": 0,
    "period_start_date": None,
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
    "synchro_day": 1,
    "period_start_date": date(2026, 6, 1),
    "start_times": [270, None, None, None, None, None, None, None],
    "station_durations": [0, 1500, 1500, 1500, 0, 0],
}

CAPTURE_PROGRAM_SIEPE = {
    "name": "Siepe",
    "inter_station_delay": 0,
    "water_budget": 100,
    "cycle": 4,
    "week_days": 0x7F,
    "period_length": 2,
    "synchro_day": 0,
    "period_start_date": date(2026, 6, 2),
    "start_times": [1060, None, None, None, None, None, None, None],
    "station_durations": [1500, 0, 0, 0, 0, 0],
}

CAPTURE_PROGRAM_VASI = {
    "name": "Vasi",
    "inter_station_delay": 0,
    "water_budget": 100,
    "cycle": 4,
    "week_days": 0x7F,
    "period_length": 2,
    "synchro_day": 1,
    "period_start_date": date(2026, 6, 2),
    "start_times": [1080, None, None, None, None, None, None, None],
    "station_durations": [0, 0, 0, 0, 1800, 0],
}


def test_format_start_time():
    assert format_start_time(1060) == "17:40"
    assert format_start_time(270) == "04:30"
    assert format_start_time(None) == "disabled"


def test_enabled_start_count():
    assert enabled_start_count(CAPTURE_PROGRAM_A["start_times"]) == 1
    assert enabled_start_count(CAPTURE_PROGRAM_B["start_times"]) == 0


def test_format_duration():
    assert format_duration(1500) == "25 min"
    assert format_duration(65) == "1m 5s"


def test_duration_minutes():
    assert duration_minutes(1500) == 25
    assert duration_minutes(65) == 1.08


def test_schedule_summary_includes_start_times_and_station_durations():
    assert (
        schedule_summary(CAPTURE_PROGRAM_SIEPE, {1: "Siepe"})
        == "17:40 · Siepe 25 min"
    )
    assert (
        schedule_summary(
            CAPTURE_PROGRAM_C,
            {2: "Prato N", 3: "Prato S", 4: "Prato O"},
        )
        == "04:30 · Prato N 25 min, Prato S 25 min, Prato O 25 min"
    )


def test_schedule_summary_none_when_no_start_times():
    assert schedule_summary(CAPTURE_PROGRAM_B, {}) is None


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


def test_periodic_start_day_matches_program_c():
    start = date(2026, 6, 1)
    monday = datetime(2026, 6, 1, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    thursday = datetime(2026, 6, 4, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    friday = datetime(2026, 6, 5, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    assert periodic_start_day_matches(monday, start, 3)
    assert periodic_start_day_matches(thursday, start, 3)
    assert not periodic_start_day_matches(friday, start, 3)


def test_periodic_start_day_matches_phase_offset():
    start = date(2026, 6, 2)
    tuesday = datetime(2026, 6, 2, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    wednesday = datetime(2026, 6, 3, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    assert periodic_start_day_matches(tuesday, start, 2, synchro_day=0)
    assert not periodic_start_day_matches(tuesday, start, 2, synchro_day=1)
    assert periodic_start_day_matches(wednesday, start, 2, synchro_day=1)


def test_day_matches_cycle_program_c_ignores_week_days():
    tz = ZoneInfo("Europe/Rome")
    monday = datetime(2026, 6, 1, 12, 0, tzinfo=tz)
    thursday = datetime(2026, 6, 4, 12, 0, tzinfo=tz)
    friday = datetime(2026, 6, 5, 12, 0, tzinfo=tz)
    start = date(2026, 6, 1)
    assert day_matches_cycle(4, 3, 0x11, monday, period_start_date=start)
    assert day_matches_cycle(4, 3, 0x11, thursday, period_start_date=start)
    assert not day_matches_cycle(4, 3, 0x11, friday, period_start_date=start)


def test_day_matches_cycle_custom_uses_week_days_only():
    tz = ZoneInfo("Europe/Rome")
    monday = datetime(2026, 6, 1, 12, 0, tzinfo=tz)
    tuesday = datetime(2026, 6, 2, 12, 0, tzinfo=tz)
    assert day_matches_cycle(0, 0, 0x01, monday)
    assert not day_matches_cycle(0, 0, 0x01, tuesday)


def test_next_start_datetime_program_c_after_morning_on_june_1():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 6, 1, 10, 0, tzinfo=tz)
    nxt = next_start_datetime(CAPTURE_PROGRAM_C, now)
    assert nxt == datetime(2026, 6, 2, 4, 30, tzinfo=tz)


def test_next_start_datetime_respects_cycle_phase_for_siepe_and_vasi():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 6, 2, 10, 0, tzinfo=tz)

    assert next_start_datetime(CAPTURE_PROGRAM_SIEPE, now) == datetime(
        2026,
        6,
        2,
        17,
        40,
        tzinfo=tz,
    )
    assert next_start_datetime(CAPTURE_PROGRAM_VASI, now) == datetime(
        2026,
        6,
        3,
        18,
        0,
        tzinfo=tz,
    )


def test_next_start_datetime_uses_derived_phase_from_controller_anchor():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 6, 27, 10, 0, tzinfo=tz)
    program = {
        **CAPTURE_PROGRAM_C,
        "period_length": 3,
        "synchro_day": 1,
        "period_start_date": date(2026, 6, 27),
    }

    assert next_start_datetime(program, now) == datetime(
        2026,
        6,
        28,
        4,
        30,
        tzinfo=tz,
    )


def test_build_schedule_attributes_maps_station_names():
    attrs = build_schedule_attributes(
        CAPTURE_PROGRAM_C,
        {2: "Back", 3: "Side", 4: "Front"},
    )
    assert attrs["start_time_1"] == "04:30"
    assert attrs["synchro_day"] == 1
    assert attrs["period_start_date"] == "2026-06-01"
    assert attrs["next_start_approximate"] is True
    assert attrs["enabled_start_count"] == 1
    assert attrs["total_duration_minutes"] == 75
    assert attrs["Back_duration_minutes"] == 25
    assert attrs["Side_duration_minutes"] == 25


def test_schedule_context_attributes():
    attrs = schedule_context_attributes(CAPTURE_PROGRAM_C)
    assert attrs["cycle"] == 4
    assert attrs["period_length"] == 3
    assert attrs["period_start_date"] == "2026-06-01"
    assert attrs["enabled_start_count"] == 1


def test_periodic_start_day_matches_rejects_invalid_period():
    day = datetime(2026, 6, 1, tzinfo=ZoneInfo("Europe/Rome"))
    assert not periodic_start_day_matches(day, date(2026, 6, 1), 0)


def test_day_matches_cycle_pair_odd_and_even_month_days():
    tz = ZoneInfo("Europe/Rome")
    even_day = datetime(2026, 6, 2, 12, 0, tzinfo=tz)
    odd_day = datetime(2026, 6, 1, 12, 0, tzinfo=tz)
    assert day_matches_cycle(1, 0, 0, even_day)
    assert not day_matches_cycle(1, 0, 0, odd_day)
    assert day_matches_cycle(2, 0, 0, odd_day)
    assert day_matches_cycle(3, 0, 0, odd_day)
    assert not day_matches_cycle(99, 0, 0, odd_day)


def test_day_matches_cycle_four_without_start_date():
    day = datetime(2026, 6, 1, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    assert not day_matches_cycle(4, 3, 0, day, period_start_date=None)


def test_next_start_datetime_handles_naive_now():
    naive_now = datetime(2026, 6, 1, 10, 0)
    program = {
        **CAPTURE_PROGRAM_A,
        "cycle": 0,
        "week_days": 0x7F,
        "period_length": 0,
    }
    assert next_start_datetime(program, naive_now) is not None
