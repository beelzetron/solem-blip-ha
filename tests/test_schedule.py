"""Unit tests for schedule helper functions."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.solem_blip.schedule import (
    build_schedule_attributes,
    day_matches_cycle,
    duration_minutes,
    enabled_start_count,
    format_duration,
    format_start_time,
    interval_phase_valid,
    is_degenerate_schedule,
    next_start_datetime,
    schedule_context_attributes,
    schedule_summary,
    weekday_allowed,
)


def _make_program(
    cycle: int = 0,
    week_days: int = 0x7F,
    station_durations: tuple[int, ...] = (600,),
) -> dict:
    """Build a full-shape IrrigationProgram dict for degeneracy tests."""
    return {
        "name": "Test",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": cycle,
        "week_days": week_days,
        "period_length": 2,
        "synchro_day": 0,
        "period_start_date": None,
        "start_times": [480, None, None, None, None, None, None, None],
        "station_durations": list(station_durations) + [0] * (
            6 - len(station_durations)
        ),
    }

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


def _interval_program(
    *,
    period_length: int = 3,
    synchro_day: int = 0,
    start_minutes: int = 270,
) -> dict:
    """Build a cycle-4 program as a device read reports it (no anchor)."""
    return {
        "name": "Interval",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 4,
        "week_days": 0x7F,
        "period_length": period_length,
        "synchro_day": synchro_day,
        "period_start_date": None,
        "start_times": [start_minutes] + [None] * 7,
        "station_durations": [1500] + [0] * 5,
    }


def test_interval_phase_valid_range():
    assert interval_phase_valid(3, 0)
    assert interval_phase_valid(3, 2)
    assert interval_phase_valid(1, 0)
    assert not interval_phase_valid(3, 3)
    assert not interval_phase_valid(3, -1)
    assert not interval_phase_valid(0, 0)


def test_day_matches_cycle_cycle_four_is_not_a_calendar_phase():
    tz = ZoneInfo("Europe/Rome")
    monday = datetime(2026, 6, 1, 12, 0, tzinfo=tz)
    assert not day_matches_cycle(4, 0x11, monday)


def test_day_matches_cycle_custom_uses_week_days_only():
    tz = ZoneInfo("Europe/Rome")
    monday = datetime(2026, 6, 1, 12, 0, tzinfo=tz)
    tuesday = datetime(2026, 6, 2, 12, 0, tzinfo=tz)
    assert day_matches_cycle(0, 0x01, monday)
    assert not day_matches_cycle(0, 0x01, tuesday)


def test_next_start_datetime_interval_countdown_today():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 19, 3, 0, tzinfo=tz)
    program = _interval_program(period_length=3, synchro_day=0)

    assert next_start_datetime(program, now) == datetime(
        2026, 10, 19, 4, 30, tzinfo=tz
    )


def test_next_start_datetime_interval_passed_slot_rolls_one_period():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 19, 10, 0, tzinfo=tz)
    program = _interval_program(period_length=3, synchro_day=0)

    assert next_start_datetime(program, now) == datetime(
        2026, 10, 22, 4, 30, tzinfo=tz
    )


def test_next_start_datetime_interval_counts_down_from_today():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 19, 10, 0, tzinfo=tz)
    program = _interval_program(period_length=3, synchro_day=2)

    assert next_start_datetime(program, now) == datetime(
        2026, 10, 21, 4, 30, tzinfo=tz
    )


def test_next_start_datetime_interval_period_beyond_lookahead():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 19, 10, 0, tzinfo=tz)
    program = _interval_program(period_length=20, synchro_day=5)

    assert next_start_datetime(program, now) == datetime(
        2026, 10, 24, 4, 30, tzinfo=tz
    )


def test_next_start_datetime_interval_long_period_covers_full_cycle():
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 19, 10, 0, tzinfo=tz)
    program = _interval_program(period_length=20, synchro_day=0)

    # The 04:30 slot already passed today, so the next run is a whole
    # 20-day period out - well past the 14-day lookahead window.
    assert next_start_datetime(program, now) == datetime(
        2026, 11, 8, 4, 30, tzinfo=tz
    )


@pytest.mark.parametrize(
    ("period_length", "synchro_day"),
    [(3, 3), (3, -1), (1, 1), (0, 0)],
)
def test_next_start_datetime_interval_invalid_phase_is_none(
    period_length: int, synchro_day: int
) -> None:
    now = datetime(2026, 10, 19, 10, 0, tzinfo=ZoneInfo("Europe/Rome"))
    program = _interval_program(
        period_length=period_length, synchro_day=synchro_day
    )

    assert next_start_datetime(program, now) is None


def test_next_start_datetime_interval_last_valid_phase() -> None:
    """``synchro_day == period_length - 1`` is the latest legal countdown."""
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 19, 10, 0, tzinfo=tz)
    program = _interval_program(period_length=5, synchro_day=4)

    assert interval_phase_valid(5, 4)
    assert next_start_datetime(program, now) == datetime(
        2026, 10, 23, 4, 30, tzinfo=tz
    )


def test_next_start_datetime_interval_max_period_length() -> None:
    """A 255-day period with a valid countdown still yields its run day."""
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 19, 10, 0, tzinfo=tz)
    program = _interval_program(period_length=255, synchro_day=254)

    assert interval_phase_valid(255, 254)
    assert next_start_datetime(program, now) == datetime(
        2027, 6, 30, 4, 30, tzinfo=tz
    )


def test_next_start_datetime_interval_naive_now_uses_default_zone() -> None:
    """A naive ``now`` is interpreted in HA's default timezone, not localhost."""
    naive_now = datetime(2026, 10, 19, 10, 0)
    program = _interval_program(period_length=3, synchro_day=2)

    result = next_start_datetime(program, naive_now)

    assert result == datetime(2026, 10, 21, 4, 30, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    assert result is not None
    assert result.hour == 4 and result.minute == 30


def test_next_start_datetime_interval_multiple_slots_earliest_after_now() -> None:
    """Only the first enabled slot after ``now`` on the run day wins."""
    tz = ZoneInfo("Europe/Rome")
    # Slot 1 is 10:00 and slot 2 is 04:30; the run day is today itself.
    program = {
        **_interval_program(period_length=3, synchro_day=0),
        "start_times": [600, 270, None, None, None, None, None, None],
    }

    # 03:00 is before both slots: the earliest one wins, not the first slot.
    assert next_start_datetime(program, datetime(2026, 10, 19, 3, 0, tzinfo=tz)) == (
        datetime(2026, 10, 19, 4, 30, tzinfo=tz)
    )
    # 05:00 has passed 04:30: the next slot on the same run day wins, so the
    # result does not roll a whole period out.
    assert next_start_datetime(program, datetime(2026, 10, 19, 5, 0, tzinfo=tz)) == (
        datetime(2026, 10, 19, 10, 0, tzinfo=tz)
    )
    # Past the last slot the countdown rolls one period, keeping 04:30.
    assert next_start_datetime(program, datetime(2026, 10, 19, 11, 0, tzinfo=tz)) == (
        datetime(2026, 10, 22, 4, 30, tzinfo=tz)
    )


@contextmanager
def _local_zone(zone: str):
    """Temporarily set HA's default timezone for a test block."""
    previous = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone(zone))
    try:
        yield
    finally:
        dt_util.set_default_time_zone(previous)


def test_next_start_datetime_interval_fixed_offset_crosses_spring_forward() -> None:
    """A fixed-offset ``now`` keeps the wall-clock slot across spring-forward.

    ``coordinator_descriptors`` passes ``datetime.now().astimezone()``, whose
    offset is frozen at the moment it was taken. Adding a multi-day countdown
    across the March transition used to keep the winter offset and return an
    instant an hour off the 04:30 slot; normalising to HA's ZoneInfo fixes it.
    """
    rome = ZoneInfo("Europe/Rome")
    # 10:00 on 15 March 2026 in Rome is UTC+1: a fixed-offset copy of it.
    now = datetime(2026, 3, 15, 10, 0, tzinfo=timezone(timedelta(hours=1)))
    program = _interval_program(period_length=20, synchro_day=15)

    with _local_zone("Europe/Rome"):
        result = next_start_datetime(program, now)

        assert result == datetime(2026, 3, 30, 4, 30, tzinfo=rome)
        assert result is not None
        assert result.utcoffset() == timedelta(hours=2)
        assert dt_util.as_local(result).hour == 4
        assert dt_util.as_local(result).minute == 30


def test_next_start_datetime_interval_fixed_offset_crosses_fall_back() -> None:
    """A fixed-offset ``now`` keeps the wall-clock slot across fall-back.

    The reported case: a period-20 countdown taken in October used to place
    the November run at 04:30+02:00 instead of the local 04:30+01:00.
    """
    rome = ZoneInfo("Europe/Rome")
    now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone(timedelta(hours=2)))
    program = _interval_program(period_length=20, synchro_day=15)

    with _local_zone("Europe/Rome"):
        result = next_start_datetime(program, now)

        assert result == datetime(2026, 10, 25, 4, 30, tzinfo=rome)
        assert result is not None
        assert result.utcoffset() == timedelta(hours=1)
        assert dt_util.as_local(result).hour == 4
        assert dt_util.as_local(result).minute == 30


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


def test_next_start_datetime_interval_ignores_client_anchor():
    """The countdown, not a stored anchor, places the next interval run."""
    tz = ZoneInfo("Europe/Rome")
    now = datetime(2026, 6, 27, 10, 0, tzinfo=tz)
    program = {
        **CAPTURE_PROGRAM_C,
        "period_length": 3,
        "synchro_day": 1,
        "period_start_date": date(2026, 6, 27),
    }

    anchored = next_start_datetime(program, now)
    program["period_start_date"] = None

    assert anchored == datetime(2026, 6, 28, 4, 30, tzinfo=tz)
    assert next_start_datetime(program, now) == anchored


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


def test_schedule_attributes_expose_controller_date():
    program = {**CAPTURE_PROGRAM_C, "controller_date": date(2026, 10, 19)}

    attrs = build_schedule_attributes(program, {})
    context = schedule_context_attributes(program)

    assert attrs["controller_date"] == "2026-10-19"
    assert context["controller_date"] == "2026-10-19"
    # Cycle 4 stays explicitly approximate: the countdown is a client-side
    # model, not a controller guarantee.
    assert attrs["next_start_approximate"] is True


def test_schedule_attributes_omit_absent_dates():
    """A device read carries neither the anchor nor a header date."""
    program = {
        **CAPTURE_PROGRAM_C,
        "period_start_date": None,
        "controller_date": None,
    }

    attrs = build_schedule_attributes(program, {})
    context = schedule_context_attributes(program)

    assert "period_start_date" not in attrs
    assert "controller_date" not in attrs
    assert "period_start_date" not in context
    assert "controller_date" not in context


def test_day_matches_cycle_pair_odd_and_even_month_days():
    tz = ZoneInfo("Europe/Rome")
    even_day = datetime(2026, 6, 2, 12, 0, tzinfo=tz)
    odd_day = datetime(2026, 6, 1, 12, 0, tzinfo=tz)
    assert day_matches_cycle(1, 0, even_day)
    assert not day_matches_cycle(1, 0, odd_day)
    assert day_matches_cycle(2, 0, odd_day)
    assert day_matches_cycle(3, 0, odd_day)
    assert not day_matches_cycle(3, 0, datetime(2026, 7, 31, 12, 0, tzinfo=tz))
    assert not day_matches_cycle(99, 0, odd_day)


def test_next_start_datetime_handles_naive_now():
    naive_now = datetime(2026, 6, 1, 10, 0)
    program = {
        **CAPTURE_PROGRAM_A,
        "cycle": 0,
        "week_days": 0x7F,
        "period_length": 0,
    }
    assert next_start_datetime(program, naive_now) is not None


def test_week_days_zero_is_degenerate():
    assert is_degenerate_schedule(_make_program(cycle=0, week_days=0)) == "no_days"


def test_all_zero_durations_is_degenerate():
    program = _make_program(cycle=0, week_days=127, station_durations=(0,))
    assert is_degenerate_schedule(program) == "no_durations"


def test_healthy_program_is_not_degenerate():
    program = _make_program(cycle=0, week_days=127, station_durations=(600,))
    assert is_degenerate_schedule(program) is None


def test_no_start_slots_is_degenerate():
    program = _make_program(cycle=0, week_days=127, station_durations=(600,))
    program["start_times"] = [None] * 8
    assert is_degenerate_schedule(program) == "no_starts"


def test_no_starts_not_flagged_when_other_reason_first():
    program = _make_program(cycle=0, week_days=127, station_durations=(0,))
    program["start_times"] = [None] * 8
    # Ordering is pinned: durations first, then days, then starts.
    assert is_degenerate_schedule(program) == "no_durations"


def test_all_zero_durations_degenerate_for_every_cycle():
    program = _make_program(cycle=4, week_days=127, station_durations=(0,))
    assert is_degenerate_schedule(program) == "no_durations"


def test_periodic_cycle_ignores_week_days():
    program = _make_program(cycle=4, week_days=0, station_durations=(600,))
    assert is_degenerate_schedule(program) is None
