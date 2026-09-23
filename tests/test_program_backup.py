"""Tests for persistent irrigation program backups."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.solem_blip.program_backup import ProgramBackupStore


PROGRAMS = {
    0: {
        "name": "Morning",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 0,
        "week_days": 0x7F,
        "period_length": 1,
        "synchro_day": 0,
        "period_start_date": date(2026, 6, 1),
        "start_times": [360, None, None, None, None, None, None, None],
        "station_durations": [60, 120],
    },
    1: {
        "name": "Empty",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 0,
        "week_days": 0,
        "period_length": 1,
        "synchro_day": 0,
        "period_start_date": None,
        "start_times": [None] * 8,
        "station_durations": [0, 0],
    },
}


@pytest.mark.asyncio
async def test_backup_round_trip(hass: HomeAssistant) -> None:
    """A non-empty backup survives a new store instance and restores dates."""
    backup = ProgramBackupStore(hass, "entry")
    assert await backup.async_save_if_non_empty(PROGRAMS)

    restored = ProgramBackupStore(hass, "entry")
    await restored.async_load()

    assert restored.programs == PROGRAMS
    assert restored.programs[0]["period_start_date"] == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_empty_programs_do_not_replace_backup(hass: HomeAssistant) -> None:
    """An empty controller read preserves the last useful backup."""
    backup = ProgramBackupStore(hass, "entry")
    assert await backup.async_save_if_non_empty(PROGRAMS)

    empty = {
        index: {
            **program,
            "start_times": [None] * 8,
            "station_durations": [0, 0],
        }
        for index, program in PROGRAMS.items()
    }
    assert not await backup.async_save_if_non_empty(empty)
    assert backup.programs == PROGRAMS

    restored = ProgramBackupStore(hass, "entry")
    await restored.async_load()
    assert restored.programs == PROGRAMS


@pytest.mark.asyncio
async def test_backup_property_is_defensive_copy(hass: HomeAssistant) -> None:
    """Callers cannot mutate the stored in-memory snapshot."""
    backup = ProgramBackupStore(hass, "entry")
    await backup.async_save_if_non_empty(PROGRAMS)

    programs = backup.programs
    programs[0]["name"] = "Changed"

    assert backup.programs[0]["name"] == "Morning"


@pytest.mark.asyncio
async def test_non_empty_programs_do_not_replace_existing_backup(
    hass: HomeAssistant,
) -> None:
    """A later useful controller read cannot replace the restore snapshot."""
    backup = ProgramBackupStore(hass, "entry")
    assert await backup.async_save_if_non_empty(PROGRAMS)

    changed = {
        **PROGRAMS,
        0: {
            **PROGRAMS[0],
            "name": "test 2",
            "cycle": 0,
            "week_days": 0,
            "synchro_day": 0,
            "start_times": [None] * 8,
        },
        1: {
            **PROGRAMS[1],
            "name": "Still scheduled",
            "start_times": [1200, None, None, None, None, None, None, None],
            "station_durations": [0, 1200],
        },
    }

    assert not await backup.async_save_if_non_empty(changed)
    assert backup.programs == PROGRAMS

    restored = ProgramBackupStore(hass, "entry")
    await restored.async_load()
    assert restored.programs == PROGRAMS


@pytest.mark.asyncio
async def test_abort_restore_clears_only_pending_journal(hass: HomeAssistant) -> None:
    """A confirmed preflight abort preserves the protected program backup."""
    backup = ProgramBackupStore(hass, "entry")
    await backup.async_save_if_non_empty(PROGRAMS)
    before = MagicMock()
    before.revision = "before"
    before.frames = ()
    expected = MagicMock()
    expected.revision = "expected"
    expected.frames = ()
    await backup.async_begin_restore(before, expected)

    await backup.async_abort_restore()

    assert backup.pending is None
    assert backup.programs == PROGRAMS


@pytest.mark.asyncio
async def test_pending_restore_reconciles_expected_revision(
    hass: HomeAssistant,
) -> None:
    """A fresh expected read confirms and stores the restored raw snapshot."""
    backup = ProgramBackupStore(hass, "entry")
    before = MagicMock()
    before.revision = "before"
    before.frames = ()
    expected = MagicMock()
    expected.revision = "expected"
    expected.frames = ()
    await backup.async_begin_restore(before, expected)

    current = MagicMock()
    current.revision = "expected"
    current.frames = ()
    assert await backup.async_reconcile(current)
    assert backup.pending is None
    assert backup.snapshot is current


@pytest.mark.asyncio
async def test_pending_restore_before_revision_preserves_protected_snapshot(
    hass: HomeAssistant,
) -> None:
    """A fresh before read clears the journal without replacing the backup."""
    backup = ProgramBackupStore(hass, "entry")
    protected = MagicMock()
    protected.revision = "protected"
    protected.frames = ()
    await backup.async_finish_restore(protected)

    before = MagicMock()
    before.revision = "before"
    before.frames = ()
    expected = MagicMock()
    expected.revision = "expected"
    expected.frames = ()
    await backup.async_begin_restore(before, expected)

    current = MagicMock()
    current.revision = "before"
    current.frames = ()
    assert await backup.async_reconcile(current)
    assert backup.pending is None
    assert backup.snapshot is protected


@pytest.mark.asyncio
async def test_pending_restore_before_revision_recovers_expected_raw_snapshot(
    hass: HomeAssistant,
) -> None:
    """A legacy store without raw frames recovers them from the restore journal."""
    backup = ProgramBackupStore(hass, "entry")
    before = MagicMock()
    before.revision = "before"
    before.frames = (b"before",)
    expected = MagicMock()
    expected.revision = "expected"
    expected.frames = (b"expected",)
    await backup.async_begin_restore(before, expected)

    recovered = MagicMock()
    recovered.frames = expected.frames
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.solem_blip.program_backup.ProgramSnapshot.from_frames",
            lambda frames: recovered,
        )
        current = MagicMock()
        current.revision = "before"
        current.frames = before.frames
        assert await backup.async_reconcile(current)

    assert backup.pending is None
    assert backup.snapshot is recovered


@pytest.mark.asyncio
async def test_pending_restore_keeps_unknown_revision_blocked(
    hass: HomeAssistant,
) -> None:
    """A divergent fresh read cannot silently resolve an uncertain write."""
    backup = ProgramBackupStore(hass, "entry")
    before = MagicMock()
    before.revision = "before"
    before.frames = ()
    expected = MagicMock()
    expected.revision = "expected"
    expected.frames = ()
    await backup.async_begin_restore(before, expected)

    current = MagicMock()
    current.revision = "different"
    current.frames = ()
    assert not await backup.async_reconcile(current)
    assert backup.pending is not None
    assert backup.snapshot is None
