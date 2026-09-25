"""Tests for persistent irrigation program backups."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from solem_blip_ble.snapshot import InvalidSnapshot

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
async def test_load_repairs_mixed_raw_snapshot_without_ble(
    hass: HomeAssistant,
) -> None:
    """A beta.12 mixed store is repaired from its protected logical programs."""
    backup = ProgramBackupStore(hass, "mixed")
    await backup.async_save_if_non_empty(PROGRAMS)

    degraded_programs = {
        index: dict(program) for index, program in PROGRAMS.items()
    }
    degraded_programs[0] = {
        **PROGRAMS[0],
        "start_times": [None] * 8,
        "station_durations": [0, 0],
    }
    degraded = MagicMock()
    degraded.programs = degraded_programs
    degraded.frames = (b"raw-hidden-slots",)
    degraded.revision = "degraded"
    await backup.async_finish_restore(degraded)

    repaired = MagicMock()
    repaired.programs = PROGRAMS
    repaired.frames = (b"repaired-hidden-slots",)
    repaired.revision = "repaired"
    degraded.patch.return_value = ([b"write"], repaired)

    loaded = ProgramBackupStore(hass, "mixed")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.solem_blip.program_backup.ProgramSnapshot.from_frames",
            lambda frames: degraded,
        )
        await loaded.async_load()

    assert loaded.programs == PROGRAMS
    assert loaded.snapshot is repaired
    degraded.patch.assert_called_once()


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


@pytest.mark.asyncio
async def test_pending_restore_reconciles_controller_normalized_dates(
    hass: HomeAssistant,
) -> None:
    """A beta.16 journal accepts only controller-normalized A/B/C dates."""
    backup = ProgramBackupStore(hass, "normalized")
    before = MagicMock()
    before.revision = "before"
    before.frames = ()

    expected_frame = bytes.fromhex("3a0e431200000064047f0705160907ea")
    current_frame = bytes.fromhex("3a0e431200000064047f0705170907ea")
    expected = MagicMock()
    expected.revision = "expected"
    expected.frames = (expected_frame,)
    await backup.async_begin_restore(before, expected)

    current = MagicMock()
    current.revision = "normalized"
    current.frames = (current_frame,)
    parsed_expected = MagicMock()
    parsed_expected.frames = expected.frames

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.solem_blip.program_backup.ProgramSnapshot.from_frames",
            lambda frames: parsed_expected,
        )
        assert await backup.async_reconcile(current)

    assert backup.pending is None
    assert backup.snapshot is current


@pytest.mark.asyncio
async def test_pending_restore_rejects_other_normalized_divergence(
    hass: HomeAssistant,
) -> None:
    """A beta.16 journal stays blocked when any non-date byte differs."""
    backup = ProgramBackupStore(hass, "normalized-other")
    before = MagicMock()
    before.revision = "before"
    before.frames = ()

    expected_frame = bytes.fromhex("3a0e431200000064047f0705160907ea")
    current_frame = bytes.fromhex("3a0e431200000064047f0705170907eb")
    expected = MagicMock()
    expected.revision = "expected"
    expected.frames = (expected_frame,)
    await backup.async_begin_restore(before, expected)

    current = MagicMock()
    current.revision = "different"
    current.frames = (current_frame,)
    parsed_expected = MagicMock()
    parsed_expected.frames = expected.frames

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.solem_blip.program_backup.ProgramSnapshot.from_frames",
            lambda frames: parsed_expected,
        )
        assert not await backup.async_reconcile(current)

    assert backup.pending is not None


def test_normalized_date_match_requires_same_complete_frame_shape() -> None:
    """Normalized-date reconciliation rejects incomplete or identical snapshots."""
    from custom_components.solem_blip.program_backup import (
        _differs_only_by_period_start_date,
    )

    frame = bytes.fromhex("3a0e431200000064047f0705160907ea")
    current = MagicMock()
    expected = MagicMock()

    current.frames = (frame,)
    expected.frames = (frame,)
    assert not _differs_only_by_period_start_date(current, expected)

    current.frames = (frame, frame)
    expected.frames = (frame,)
    assert not _differs_only_by_period_start_date(current, expected)


def test_normalized_date_match_rejects_non_header_and_non_abc_frames() -> None:
    """Only A/B/C program header day bytes may be normalized."""
    from custom_components.solem_blip.program_backup import (
        _differs_only_by_period_start_date,
    )

    current = MagicMock()
    expected = MagicMock()

    expected.frames = (bytes.fromhex("3a12421204b005a005a005a005a005a005a005a0"),)
    current.frames = (bytes.fromhex("3a12421205a005a005a005a005a005a005a005a0"),)
    assert not _differs_only_by_period_start_date(current, expected)

    expected.frames = (bytes.fromhex("3a0e3c1300000064007f0200160907ea"),)
    current.frames = (bytes.fromhex("3a0e3c1300000064007f0200170907ea"),)
    assert not _differs_only_by_period_start_date(current, expected)


@pytest.mark.asyncio
async def test_explicit_replace_updates_logical_and_raw_backup(
    hass: HomeAssistant,
) -> None:
    """An explicit valid snapshot replaces programs and raw frames together."""
    backup = ProgramBackupStore(hass, "replace")
    await backup.async_save_if_non_empty(PROGRAMS)

    changed = {
        **PROGRAMS,
        0: {**PROGRAMS[0], "name": "Updated"},
        2: {**PROGRAMS[1], "name": "Program C"},
    }
    requested = MagicMock()
    requested.frames = (b"complete-new-snapshot",)
    validated = MagicMock()
    validated.frames = requested.frames
    validated.programs = changed

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.solem_blip.program_backup.ProgramSnapshot.from_frames",
            lambda frames: validated,
        )
        await backup.async_replace(requested)

    assert backup.programs == changed
    assert backup.snapshot is validated


@pytest.mark.asyncio
async def test_explicit_replace_invalid_snapshot_preserves_backup(
    hass: HomeAssistant,
) -> None:
    """A snapshot validation failure leaves the protected backup untouched."""
    backup = ProgramBackupStore(hass, "replace-invalid")
    await backup.async_save_if_non_empty(PROGRAMS)
    old_programs = backup.programs
    old_snapshot = backup.snapshot

    requested = MagicMock()
    requested.frames = (b"partial",)

    with pytest.MonkeyPatch.context() as monkeypatch:
        def _invalid(frames):
            raise InvalidSnapshot("partial snapshot")

        monkeypatch.setattr(
            "custom_components.solem_blip.program_backup.ProgramSnapshot.from_frames",
            _invalid,
        )
        with pytest.raises(InvalidSnapshot):
            await backup.async_replace(requested)

    assert backup.programs == old_programs
    assert backup.snapshot is old_snapshot


@pytest.mark.asyncio
async def test_explicit_replace_pending_restore_preserves_backup(
    hass: HomeAssistant,
) -> None:
    """A pending restore blocks explicit protected-backup replacement."""
    backup = ProgramBackupStore(hass, "replace-pending")
    await backup.async_save_if_non_empty(PROGRAMS)
    old_programs = backup.programs

    before = MagicMock(revision="before", frames=())
    expected = MagicMock(revision="expected", frames=())
    await backup.async_begin_restore(before, expected)

    requested = MagicMock(frames=(b"complete",))
    with pytest.raises(InvalidSnapshot):
        await backup.async_replace(requested)

    assert backup.programs == old_programs
    assert backup.pending is not None


@pytest.mark.asyncio
async def test_explicit_replace_persistence_failure_rolls_back_memory(
    hass: HomeAssistant,
) -> None:
    """A failed durable save keeps the previous in-memory restore point."""
    backup = ProgramBackupStore(hass, "replace-save-failure")
    await backup.async_save_if_non_empty(PROGRAMS)
    old_programs = backup.programs
    old_snapshot = backup.snapshot

    changed = {
        **PROGRAMS,
        0: {**PROGRAMS[0], "name": "Updated"},
        2: {**PROGRAMS[1], "name": "Program C"},
    }
    requested = MagicMock(frames=(b"complete",))
    validated = MagicMock(frames=requested.frames, programs=changed)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.solem_blip.program_backup.ProgramSnapshot.from_frames",
            lambda frames: validated,
        )
        monkeypatch.setattr(
            backup._store,
            "async_save",
            AsyncMock(side_effect=RuntimeError("disk failure")),
        )
        with pytest.raises(RuntimeError, match="disk failure"):
            await backup.async_replace(requested)

    assert backup.programs == old_programs
    assert backup.snapshot is old_snapshot
