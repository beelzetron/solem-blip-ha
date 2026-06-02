"""Schedule helpers for Solem BL-IP irrigation programs."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

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


def periodic_start_day_matches(
    day: datetime,
    period_start_date: date,
    period_length: int,
    synchro_day: int = 0,
) -> bool:
    """Return whether ``day`` is on a periodic run day (start date + every N days)."""
    if period_length <= 0:
        return False
    delta_days = (day.date() - period_start_date).days
    return delta_days >= 0 and delta_days % period_length == synchro_day % period_length


def day_matches_cycle(
    cycle: int,
    period_length: int,
    week_days: int,
    day: datetime,
    *,
    period_start_date: date | None = None,
    synchro_day: int = 0,
) -> bool:
    """Return whether ``day`` matches the program cycle mode."""
    day_of_month = day.day
    if cycle == 0:
        return weekday_allowed(week_days, day)
    if cycle == 1:
        return day_of_month % 2 == 0
    if cycle == 2:
        return day_of_month % 2 == 1
    if cycle == 3:
        return day_of_month % 2 == 1 and day_of_month != 31
    if cycle == 4:
        if period_start_date is None:
            return False
        return periodic_start_day_matches(
            day,
            period_start_date,
            period_length,
            synchro_day,
        )
    return False


def enabled_start_count(start_times: list[int | None]) -> int:
    """Count enabled start-time slots."""
    return sum(1 for minutes in start_times if minutes is not None)


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


def next_start_datetime(
    program: IrrigationProgram,
    now: datetime,
) -> datetime | None:
    """Return the next scheduled start in local time, or ``None`` if none."""
    enabled = [minutes for minutes in program["start_times"] if minutes is not None]
    if not enabled:
        return None

    cycle = program["cycle"]
    period_length = program["period_length"]
    synchro_day = program.get("synchro_day", 0)
    week_days = program["week_days"]
    period_start = program.get("period_start_date")
    if now.tzinfo is None:
        local_now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    else:
        local_now = now
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    for offset in range(_MAX_LOOKAHEAD_DAYS):
        candidate_day = day_start + timedelta(days=offset)
        if not day_matches_cycle(
            cycle,
            period_length,
            week_days,
            candidate_day,
            period_start_date=period_start,
            synchro_day=synchro_day,
        ):
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
    return attrs
