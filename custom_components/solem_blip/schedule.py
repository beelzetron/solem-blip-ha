"""Schedule helpers for Solem BL-IP irrigation programs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from solem_blip_ble import IrrigationProgram

_MAX_LOOKAHEAD_DAYS = 14


def format_start_time(minutes: int | None) -> str:
    """Format minutes since midnight as HH:MM or ``disabled``."""
    if minutes is None:
        return "disabled"
    hours, mins = divmod(minutes, 60)
    return f"{hours:02d}:{mins:02d}"


def weekday_allowed(week_days: int, day: datetime) -> bool:
    """Return whether ``day`` matches the V5 weekday bitmask (Mon=bit0 … Sun=bit6)."""
    return bool(week_days & (1 << day.weekday()))


def interval_phase_valid(period_length: int, synchro_day: int) -> bool:
    """Return whether a cycle-4 interval phase is inside its valid range.

    ``synchro_day`` is a countdown from the write day to the next run, so the
    only meaningful values are ``0 <= synchro_day < period_length``. Anything
    else is a captured or submitted phase the client cannot interpret, and the
    next start is unknown rather than guessed.
    """
    return period_length >= 1 and 0 <= synchro_day < period_length


def day_matches_cycle(
    cycle: int,
    week_days: int,
    day: datetime,
) -> bool:
    """Return whether ``day`` matches the program cycle mode.

    Cycle 4 (periodic/interval) is deliberately not answerable per day: the
    device stores no absolute anchor, only a countdown phase, so interval run
    days come from ``next_start_datetime``'s countdown path instead of a
    calendar phase comparison. This helper always returns ``False`` for it.
    """
    day_of_month = day.day
    if cycle == 0:
        return weekday_allowed(week_days, day)
    if cycle == 1:
        return day_of_month % 2 == 0
    if cycle == 2:
        return day_of_month % 2 == 1
    if cycle == 3:
        return day_of_month % 2 == 1 and day_of_month != 31
    return False


def enabled_start_count(start_times: list[int | None]) -> int:
    """Count enabled start-time slots."""
    return sum(1 for minutes in start_times if minutes is not None)


DegenerateReason = Literal["no_days", "no_durations", "no_starts"]


def is_degenerate_schedule(program: IrrigationProgram) -> DegenerateReason | None:
    """Return why a program can never run, or None if it is runnable.

    Three degenerate shapes are detected:

    - "no_durations": every station duration is zero, so the program
      would water for no time even when a start day and slot match.
    - "no_days": the weekly cycle (cycle 0) is selected but no weekday
      bit is set in ``week_days``, so ``weekday_allowed`` rejects every
      day of the week and no start can ever fire. This check only
      applies to cycle 0: cycles 1-3 pick days by day-of-month parity
      and cycle 4 is periodic — none of them consult ``week_days``, so
      ``week_days == 0`` is harmless there.
    - "no_starts": every start-time slot is disabled, so no start can
      ever fire regardless of days or durations. The official app uses
      exactly this shape as its disable idiom (clearing all start
      times), so the editor treats it as a warning-with-confirm rather
      than an error.
    """
    if not any(d > 0 for d in program["station_durations"]):
        return "no_durations"
    if int(program["cycle"]) == 0 and int(program["week_days"]) == 0:
        return "no_days"
    if enabled_start_count(program["start_times"]) == 0:
        return "no_starts"
    return None


def format_duration(seconds: int) -> str:
    """Format a duration in seconds for schedule summaries."""
    minutes, remaining_seconds = divmod(seconds, 60)
    if remaining_seconds:
        return f"{minutes}m {remaining_seconds}s"
    return f"{minutes} min"


def duration_minutes(seconds: int) -> int | float:
    """Convert duration seconds to minutes, preserving sub-minute precision."""
    minutes, remaining_seconds = divmod(seconds, 60)
    if remaining_seconds:
        return round(seconds / 60, 2)
    return minutes


def schedule_summary(
    program: IrrigationProgram,
    station_names: dict[int, str],
) -> str | None:
    """Return a concise human-readable summary for a program schedule."""
    start_times = [format_start_time(minutes) for minutes in program["start_times"] if minutes is not None]
    if not start_times:
        return None

    durations = []
    for station_id, duration in enumerate(program["station_durations"], start=1):
        if duration <= 0:
            continue
        name = station_names.get(station_id) or f"Station {station_id}"
        durations.append(f"{name} {format_duration(duration)}")

    starts = ", ".join(start_times)
    if not durations:
        return starts
    return f"{starts} · {', '.join(durations)}"


def _next_interval_start(
    enabled: list[int],
    period_length: int,
    synchro_day: int,
    local_now: datetime,
) -> datetime | None:
    """Return the next cycle-4 start from the countdown phase, or ``None``.

    ``synchro_day`` is the controller's countdown in days from the write day to
    the next run, so the first run day is local midnight today plus
    ``synchro_day`` and later runs repeat every ``period_length`` days. The
    scan is not clamped to ``_MAX_LOOKAHEAD_DAYS``: a period longer than the
    lookahead window still yields its next run date.
    """
    if not interval_phase_valid(period_length, synchro_day):
        return None
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    base_day = day_start + timedelta(days=synchro_day)
    # Offset 0 is the countdown's own run day; one further period covers a
    # start slot that has already passed today.
    span = max(_MAX_LOOKAHEAD_DAYS, period_length * 2 + 1)
    for offset in range(0, span, period_length):
        candidate_day = base_day + timedelta(days=offset)
        for minutes in sorted(enabled):
            hour, minute = divmod(minutes, 60)
            candidate = candidate_day.replace(hour=hour, minute=minute)
            if candidate > local_now:
                return candidate
    return None


def next_start_datetime(
    program: IrrigationProgram,
    now: datetime,
) -> datetime | None:
    """Return the next scheduled start in local time, or ``None`` if none."""
    enabled = [minutes for minutes in program["start_times"] if minutes is not None]
    if not enabled:
        return None

    cycle = program["cycle"]
    if now.tzinfo is None:
        local_now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    else:
        local_now = now
    if cycle == 4:
        return _next_interval_start(
            enabled,
            program["period_length"],
            program.get("synchro_day", 0),
            local_now,
        )

    week_days = program["week_days"]
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(_MAX_LOOKAHEAD_DAYS):
        candidate_day = day_start + timedelta(days=offset)
        if not day_matches_cycle(cycle, week_days, candidate_day):
            continue
        for minutes in sorted(enabled):
            hour, minute = divmod(minutes, 60)
            candidate = candidate_day.replace(hour=hour, minute=minute)
            if candidate > local_now:
                return candidate
    return None


def build_schedule_attributes(
    program: IrrigationProgram,
    station_names: dict[int, str],
) -> dict[str, Any]:
    """Build extra state attributes for a program schedule summary sensor."""
    period_start = program.get("period_start_date")
    attrs: dict[str, Any] = {
        "water_budget": program["water_budget"],
        "cycle": program["cycle"],
        "week_days": program["week_days"],
        "period_length": program["period_length"],
        "synchro_day": program.get("synchro_day", 0),
        "inter_station_delay": program["inter_station_delay"],
        "enabled_start_count": enabled_start_count(program["start_times"]),
        "total_duration_minutes": duration_minutes(
            sum(duration for duration in program["station_durations"] if duration > 0)
        ),
    }
    if period_start is not None:
        attrs["period_start_date"] = period_start.isoformat()
    controller_date = program.get("controller_date")
    if controller_date is not None:
        attrs["controller_date"] = controller_date.isoformat()
    if program["cycle"] == 4:
        attrs["next_start_approximate"] = True
    for slot, minutes in enumerate(program["start_times"], start=1):
        attrs[f"start_time_{slot}"] = format_start_time(minutes)
    for station_id, duration in enumerate(program["station_durations"], start=1):
        if duration <= 0:
            continue
        name = station_names.get(station_id) or f"Station {station_id}"
        attrs[f"{name}_duration_minutes"] = duration_minutes(duration)
    return attrs


def schedule_context_attributes(program: IrrigationProgram) -> dict[str, Any]:
    """Subset of schedule fields for program next-start sensor attributes."""
    period_start = program.get("period_start_date")
    attrs: dict[str, Any] = {
        "cycle": program["cycle"],
        "week_days": program["week_days"],
        "period_length": program["period_length"],
        "synchro_day": program.get("synchro_day", 0),
        "enabled_start_count": enabled_start_count(program["start_times"]),
    }
    if period_start is not None:
        attrs["period_start_date"] = period_start.isoformat()
    controller_date = program.get("controller_date")
    if controller_date is not None:
        attrs["controller_date"] = controller_date.isoformat()
    return attrs
