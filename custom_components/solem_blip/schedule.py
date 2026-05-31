"""Schedule helpers for Solem BL-IP irrigation programs."""

from __future__ import annotations

from datetime import datetime, timedelta
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


def day_matches_cycle(
    cycle: int,
    period_length: int,
    week_days: int,
    day: datetime,
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
        if period_length <= 0:
            return False
        if day.toordinal() % period_length != 0:
            return False
        return weekday_allowed(week_days, day)
    return False


def enabled_start_count(start_times: list[int | None]) -> int:
    """Count enabled start-time slots."""
    return sum(1 for minutes in start_times if minutes is not None)


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
    week_days = program["week_days"]
    if now.tzinfo is None:
        local_now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    else:
        local_now = now
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    for offset in range(_MAX_LOOKAHEAD_DAYS):
        candidate_day = day_start + timedelta(days=offset)
        if not day_matches_cycle(cycle, period_length, week_days, candidate_day):
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
    attrs: dict[str, Any] = {
        "water_budget": program["water_budget"],
        "cycle": program["cycle"],
        "week_days": program["week_days"],
        "period_length": program["period_length"],
        "inter_station_delay": program["inter_station_delay"],
    }
    if program["cycle"] == 4:
        attrs["next_start_approximate"] = True
    for slot, minutes in enumerate(program["start_times"], start=1):
        attrs[f"start_time_{slot}"] = format_start_time(minutes)
    for station_id, duration in enumerate(program["station_durations"], start=1):
        if duration <= 0:
            continue
        name = station_names.get(station_id) or f"Station {station_id}"
        attrs[f"{name}_duration_seconds"] = duration
    return attrs
