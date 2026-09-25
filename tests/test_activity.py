"""Passive watering-activity attribution tests.

Source-attribution design proven out upstream in a community fork
(ThomasHFWright/solem-blip-ha PRs #1 and #4), adapted to this
integration's fixtures.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util

from custom_components.solem_blip.activity import WateringActivity
from custom_components.solem_blip.schedule import day_matches_cycle
from tests.conftest import MOCK_IRRIGATION_PROGRAMS

IDLE = {"is_watering": False, "active_program": None, "station_num": None}
RUN = {"is_watering": True, "active_program": 1, "station_num": 1}


@pytest.fixture
async def activity(coordinator, freezer):
    freezer.move_to("2026-09-18T12:00:00+00:00")
    coordinator.irrigation_programs = {
        index: dict(program)
        for index, program in MOCK_IRRIGATION_PROGRAMS.items()
    }
    coordinator.irrigation_programs[0].update(
        cycle=0, week_days=127, start_times=[720] + [None] * 7
    )
    coordinator.program_backup.last_read = dt_util.utcnow().isoformat()
    return coordinator.activity


def observe_start(activity, freezer, status=None, seconds=60):
    activity.observe(IDLE)
    freezer.tick(timedelta(seconds=seconds))
    activity.observe(status or RUN)
    return activity.current


async def test_scheduled_transitions_station_changes_delays_and_finish(
    activity, freezer
):
    # Compare the configured slot in HA local time.
    freezer.move_to("2026-09-18T11:59:00+00:00")
    activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
    at_start = dt_util.now() + timedelta(minutes=1)
    activity.c.irrigation_programs[0]["start_times"] = [
        at_start.hour * 60 + at_start.minute
    ] + [None] * 7
    assert observe_start(activity, freezer)["source"] == "Scheduled"
    activity.observe({**RUN, "station_num": 2})
    activity.observe({**RUN, "station_num": None, "is_watering": False})
    assert activity.current["stations"] == [1, 2]
    assert not activity.history
    freezer.tick(timedelta(seconds=60))
    activity.observe(IDLE)
    assert activity.current is None
    assert activity.history[0]["outcome"] == "Finished"
    assert activity.history[0]["finished_detected"] == dt_util.utcnow().isoformat()
    assert "confidence" not in activity.history[0]


async def test_external_program_and_individual_station(activity, freezer):
    activity.c.irrigation_programs[0]["start_times"] = [180] + [None] * 7
    assert observe_start(activity, freezer)["source"] == "Manual Bluetooth"
    activity.observe(IDLE)
    activity.observe(
        {"is_watering": True, "station_num": 3, "watering_origin": "manual"}
    )
    assert activity.current["source"] == "Manual Bluetooth"
    assert activity.current["program"] is None


@pytest.mark.parametrize(
    "cause",
    [
        "initial",
        "gap",
        "interval",
        "no_program",
        "stale",
        "clock",
        "revision",
        "pending",
        "unidentified",
    ],
)
async def test_ambiguous_runs_are_unknown(activity, freezer, cause):
    activity.observe(IDLE)
    status = dict(RUN)
    if cause == "initial":
        activity.last_seen = None
    if cause == "gap":
        freezer.tick(timedelta(minutes=10))
    if cause == "interval":
        # Unanchored multi-day period: a matching slot is not verifiable.
        activity.c.irrigation_programs[0].update(
            cycle=4, period_length=3, period_start_date=None
        )
    if cause == "no_program":
        activity.c.irrigation_programs = {}
    if cause == "stale":
        activity.c.program_backup.last_read = None
    if cause == "clock":
        status["time_alarm"] = True
    if cause == "revision":
        activity.last_revision = "old"
    if cause == "pending":
        activity.c.program_backup._pending = {"pending": True}
    if cause == "unidentified":
        status["active_program"] = None
    activity.observe(status)
    assert activity.current["source"] == "Unknown"


async def test_gap_and_restart_do_not_invent_finish_time(activity, freezer):
    observe_start(activity, freezer)
    freezer.tick(timedelta(minutes=10))
    activity.observe(RUN)
    assert activity.current["source"] == "Unknown"
    assert activity.history[0]["outcome"] == "Observation interrupted"
    assert activity.history[0]["finished_detected"] is None
    stored = activity._stored()
    replacement = WateringActivity(activity.c)
    replacement.store.async_load = AsyncMock(return_value=stored)
    await replacement.load()
    assert replacement.current is None
    replacement.observe(RUN)
    assert replacement.current["source"] == "Unknown"
    await replacement.shutdown()
    assert replacement.history[0]["finished_detected"] is None


async def test_ha_command_attribution_context_and_matching(
    activity, freezer, hass_admin_user
):
    context = Context(user_id=hass_admin_user.id)
    activity.observe(IDLE)
    async with activity.command(program=1, context=context):
        assert activity.pending["confirmed"] is False
    activity.observe(RUN)
    assert activity.current["source"] == "Manual Home Assistant"
    assert activity.current["user_id"] == hass_admin_user.id
    assert activity.current["actor"] == hass_admin_user.name
    assert activity.current["context_id"] == context.id
    assert activity.pending is None
    activity.observe({**RUN, "station_num": 2})
    assert not activity.history
    async with activity.command(station=3):
        pass
    activity.observe({"is_watering": True, "station_num": 3})
    assert activity.history[0]["outcome"] == "Run replaced"
    assert activity.current["source"] == "Manual Home Assistant"


@pytest.mark.parametrize("failure", [RuntimeError, __import__("asyncio").CancelledError])
async def test_uncertain_command_never_becomes_external_or_confirmed(
    activity, failure
):
    activity.observe(IDLE)
    with pytest.raises(failure):
        async with activity.command(program=1):
            raise failure()
    activity.observe(RUN)
    assert activity.current["source"] == "Unknown"


async def test_expired_and_mismatched_commands(activity, freezer):
    async with activity.command(program=2):
        pass
    activity.observe(RUN)
    assert activity.current["source"] == "Unknown"
    activity.observe(IDLE)
    freezer.tick(timedelta(minutes=6))
    activity.observe(IDLE)
    activity.c.irrigation_programs[0]["start_times"] = [180] + [None] * 7
    activity.observe(RUN)
    assert activity.current["source"] == "Manual Bluetooth"
    assert activity.pending is None


async def test_program_replacement_bounded_history_and_copy(activity, freezer):
    activity.observe(RUN)
    activity.observe({**RUN, "active_program": 2})
    assert activity.history[0]["outcome"] == "Run replaced"
    for _ in range(35):
        activity.observe(IDLE)
        activity.observe(RUN)
    assert len(activity.history) == 30
    display = activity.state
    display["history"].clear()
    display["current"]["stations"].clear()
    assert len(activity.history) == 30
    assert activity.current["stations"] == [1]


async def test_schedule_timezone_midnight_and_weekdays(activity, freezer):
    # UTC 23:00 is midnight the next day in Lisbon.
    old_zone = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Lisbon"))
    try:
        freezer.move_to("2026-09-18T22:59:00+00:00")
        activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
        activity.c.irrigation_programs[0].update(
            start_times=[0] + [None] * 7, week_days=1 << 5
        )
        assert observe_start(activity, freezer)["source"] == "Scheduled"
        activity.observe(IDLE)
        activity.c.irrigation_programs[0]["week_days"] = 1 << 4
        activity.observe(RUN)
        assert activity.current["source"] == "Manual Bluetooth"
    finally:
        dt_util.set_default_time_zone(old_zone)


async def test_pending_recovery_and_storage_failure_prevent_false_attribution(
    activity,
):
    async with activity.command(program=1, context=Context(user_id="missing-user")):
        pass
    stored = activity._stored()
    replacement = WateringActivity(activity.c)
    replacement.store.async_load = AsyncMock(return_value=stored)
    await replacement.load()
    replacement.observe(RUN)
    assert replacement.current["source"] == "Unknown"
    activity.store.async_save = AsyncMock(side_effect=OSError("disk full"))
    sent = False
    with pytest.raises(OSError):
        async with activity.command(station=1):
            sent = True
    assert not sent


async def test_logs_and_commands_are_passive(activity, freezer):
    events = []
    remove = activity.c.hass.bus.async_listen(
        "logbook_entry", lambda event: events.append(event)
    )
    activity.observe(IDLE)
    async with activity.command(program=1):
        pass
    activity.observe(RUN)
    activity.observe(IDLE)
    await activity.c.hass.async_block_till_done()
    remove()
    assert [e.data["message"] for e in events] == [
        "Started: Manual Home Assistant — Programma A",
        "Finished: Manual Home Assistant",
    ]
    activity.c.api.get_status.assert_not_awaited()
    activity.c.api.run_program_x.assert_not_awaited()


async def test_idle_after_ha_attempt_cannot_claim_a_later_external_start(activity):
    activity.observe(IDLE)
    async with activity.command(program=1):
        pass
    activity.observe(IDLE)
    assert activity.pending is None
    activity.c.irrigation_programs[0]["start_times"] = [180] + [None] * 7
    activity.observe(RUN)
    assert activity.current["source"] == "Manual Bluetooth"


@pytest.mark.parametrize(
    "cause",
    [
        None,
        "wrong_time",
        "later_station",
        "missing_remaining",
        "invalid_remaining",
        "long_gap",
        "already_running",
        "changed_revision",
        "clock_alarm",
        "new_read",
        "delay",
        "uncertain_command",
        "multiple_starts",
        "restart",
    ],
)
async def test_scheduled_start_after_missed_poll(activity, freezer, cause):
    """First-zone countdown recovers attribution without assuming every gap is scheduled."""
    freezer.move_to("2026-09-24T02:59:00+00:00")
    start = dt_util.now() + timedelta(minutes=1)
    program = activity.c.irrigation_programs[0]
    program.update(
        start_times=[start.hour * 60 + start.minute] + [None] * 7,
        station_durations=[600, 600, 600, 900, 600, 900],
        water_budget=88,
        inter_station_delay=0,
    )
    activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
    activity.observe(RUN if cause == "already_running" else IDLE)
    if cause == "uncertain_command":
        with pytest.raises(RuntimeError):
            async with activity.command(program=1):
                raise RuntimeError("reply lost")
    freezer.move_to("2026-09-24T03:04:23+00:00")
    status = {**RUN, "remaining_seconds": 224}
    if cause == "wrong_time":
        status["remaining_seconds"] = 480
    if cause == "later_station":
        status["station_num"] = 2
    if cause == "missing_remaining":
        status.pop("remaining_seconds")
    if cause == "invalid_remaining":
        status["remaining_seconds"] = 900
    if cause == "long_gap":
        activity.last_seen -= timedelta(minutes=10)
    if cause == "changed_revision":
        activity.last_revision = "old"
    if cause == "clock_alarm":
        status["time_alarm"] = True
    if cause == "new_read":
        activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
    if cause == "delay":
        program["inter_station_delay"] = 60
    if cause == "multiple_starts":
        program["start_times"][1] = program["start_times"][0] + 1
        status["remaining_seconds"] = 260
    if cause == "restart":
        activity.last_seen = None
    activity.observe(status)
    assert activity.current["source"] == ("Scheduled" if cause is None else "Unknown")
    assert activity.current["first_detected"] == dt_util.utcnow().isoformat()
    activity.c.api.run_program_x.assert_not_awaited()


def anchored_interval_program(activity, freezer, *, gap):
    """Configure program A as an anchored multi-day interval program."""
    day = dt_util.now()
    program = activity.c.irrigation_programs[0]
    start_minutes = day.hour * 60 + day.minute
    program.update(
        cycle=4,
        period_length=3,
        synchro_day=day.day % 3,
        period_start_date=day.date(),
        start_times=[start_minutes + 1] + [None] * 7,
        station_durations=[600, 600, 600, 900, 600, 900],
        water_budget=100,
        inter_station_delay=0,
    )
    assert day_matches_cycle(
        4, 3, 0x7F, day, period_start_date=day.date(), synchro_day=day.day % 3
    )
    return program, start_minutes


@pytest.mark.parametrize("gap", [False, True])
async def test_anchored_interval_run_is_scheduled(activity, freezer, gap):
    """An anchored multi-day period matching the cycle phase can be attributed."""
    freezer.move_to("2026-09-25T05:59:00+00:00")
    activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
    program, start_minutes = anchored_interval_program(activity, freezer, gap=gap)
    activity.observe(IDLE)
    if gap:
        # Miss the poll at the start slot; the first-station countdown bridges it.
        freezer.move_to("2026-09-25T06:01:05+00:00")
        status = {**RUN, "remaining_seconds": 60}
    else:
        freezer.tick(timedelta(seconds=60))
        status = RUN
    activity.observe(status)
    assert activity.current["source"] == "Scheduled"
    assert activity.current["program"] == 1
    assert activity.c.api.run_program_x.assert_not_awaited() is None


async def test_unanchored_interval_run_is_unknown(activity, freezer):
    """Unanchored multi-day periods stay Unknown even with a matching slot."""
    program = activity.c.irrigation_programs[0]
    program.update(
        cycle=4,
        period_length=3,
        start_times=[360] + [None] * 7,
        period_start_date=None,
    )
    activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
    freezer.move_to("2026-09-25T05:59:00+00:00")
    activity.observe(IDLE)
    freezer.tick(timedelta(seconds=60))
    activity.observe(RUN)
    assert activity.current["source"] == "Unknown"


async def test_gap_matching_skips_disabled_zones_and_uses_local_midnight(activity, freezer):
    old_zone = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Lisbon"))
    try:
        freezer.move_to("2026-09-18T22:59:00+00:00")
        activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
        program = activity.c.irrigation_programs[0]
        program.update(
            start_times=[0] + [None] * 7,
            week_days=1 << 5,
            station_durations=[0, 600, 0, 0, 0, 0],
            water_budget=100,
            inter_station_delay=0,
        )
        activity.observe(IDLE)
        freezer.tick(timedelta(minutes=6))
        activity.observe({**RUN, "station_num": 2, "remaining_seconds": 300})
        assert activity.current["source"] == "Scheduled"
    finally:
        dt_util.set_default_time_zone(old_zone)


async def test_command_succeeds_after_observer_consumed_intent(activity, freezer):
    """The command context must not crash when a poll already consumed the intent.

    Real sequence (hardware, minimicro34's report): start_irrigation journals
    intent, the command's BLE call succeeds, and the post-start status poll
    observes the run and consumes the intent before the command context exits.
    """
    activity.observe(IDLE)
    async with activity.command(station=1):
        # Simulate the post-start poll observing the manual run while the
        # command context is still open: intent consumed as not-yet-confirmed.
        activity.observe({"is_watering": True, "station_num": 1})
        assert activity.current["source"] == "Unknown"
        assert activity.pending is None
    # Exiting the context after consumption is a no-op, not a crash.
    assert activity.current["source"] == "Unknown"
    assert activity.current["outcome"] == "Running"
