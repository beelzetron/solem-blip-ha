"""Tests for persistent irrigation program backups."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from solem_blip_ble import protocol
from solem_blip_ble.snapshot import InvalidSnapshot, ProgramSnapshot

from custom_components.solem_blip.program_backup import (
    ProgramBackupStore,
    _program_changes,
    _serialize_program,
)


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
async def test_backup_round_trips_controller_date(hass: HomeAssistant) -> None:
    """The controller-owned header date survives persistence as a date."""
    program = {**PROGRAMS[0], "controller_date": date(2026, 10, 19)}
    backup = ProgramBackupStore(hass, "entry")
    assert await backup.async_save_if_non_empty({0: program})

    assert _serialize_program(program)["controller_date"] == "2026-10-19"

    restored = ProgramBackupStore(hass, "entry")
    await restored.async_load()

    assert restored.programs[0]["controller_date"] == date(2026, 10, 19)
    assert restored.programs[0]["period_start_date"] == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_backup_tolerates_missing_date_keys(hass: HomeAssistant) -> None:
    """Entries written without the anchor or header-date keys still load."""
    program = {
        key: value
        for key, value in PROGRAMS[0].items()
        if key not in ("period_start_date", "controller_date")
    }
    backup = ProgramBackupStore(hass, "entry")
    assert await backup.async_save_if_non_empty({0: program})

    restored = ProgramBackupStore(hass, "entry")
    await restored.async_load()

    assert "period_start_date" not in restored.programs[0]
    assert "controller_date" not in restored.programs[0]
    assert restored.programs[0]["name"] == "Morning"


def test_program_changes_never_carry_dates() -> None:
    """Restore patches must not replay the anchor or the header date.

    ``period_start_date`` is write-only (the snapshot rejects it) and the
    header date belongs to the controller, so both stay out of the patch.
    """
    changes = _program_changes(
        {**PROGRAMS[0], "controller_date": date(2026, 10, 19)}
    )

    assert "period_start_date" not in changes
    assert "controller_date" not in changes
    assert changes["synchro_day"] == 0


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


@pytest.mark.asyncio
async def test_restore_rejects_backup_with_out_of_range_phase(
    hass: HomeAssistant, coordinator, mock_solem_client
) -> None:
    """A backup the library refuses to re-encode fails before any restore starts.

    ``ProgramSnapshot.patch`` re-validates the cycle-4 interval invariant, so a
    backup captured from an out-of-range device state (``synchro_day`` not
    smaller than ``period_length``) raises ``InvalidSnapshot`` while the
    restore plan is built. The coordinator turns that into a
    ``HomeAssistantError`` naming the program slot; that text is log-only
    (both entry points wrap the call in the translated
    ``restore_programs_failed`` key), and no restore journal may be opened.
    """
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    out_of_range = {
        0: {
            **PROGRAMS[0],
            "cycle": 4,
            "period_length": 3,
            "synchro_day": 3,
        }
    }
    assert await coordinator.program_backup.async_save_if_non_empty(out_of_range)

    before = MagicMock()
    before.revision = "before"
    before.frames = ()
    before.patch.side_effect = InvalidSnapshot(
        "synchro_day 3 must be smaller than period_length 3"
    )
    mock_solem_client.get_program_snapshot = AsyncMock(return_value=before)
    mock_solem_client.write_program_frames = AsyncMock()
    coordinator.api = mock_solem_client

    with patch.object(
        coordinator.program_backup, "async_begin_restore", new=AsyncMock()
    ) as begin_restore:
        with pytest.raises(HomeAssistantError) as exc_info:
            await coordinator.restore_irrigation_programs()

    message = str(exc_info.value)
    assert "program A" in message
    assert "synchro_day 3 must be smaller than period_length 3" in message
    begin_restore.assert_not_awaited()
    mock_solem_client.write_program_frames.assert_not_awaited()
    assert coordinator.program_backup.pending is None
    assert coordinator.program_backup.programs == out_of_range


def _real_snapshot(
    header_phase: tuple[int, int, int] | None = None,
) -> ProgramSnapshot:
    """Build a genuine ``ProgramSnapshot`` from packed V5 program frames.

    ``pack_set_irrigation_program`` emits one frame per logical chunk in chunk
    order but reuses fragment ids 0/1 across the ``0x2F`` and ``0x37``
    commands, while a captured config response numbers the seven fragments
    descending. Rewriting byte 2 to ``6 - chunk`` produces the captured layout
    ``from_frames`` requires, so the result is a real library object instead of
    a mock. ``header_phase`` forges program A's header as
    ``(cycle, period_length, synchro_day)``, which is how a device-captured
    out-of-range state reaches ``patch``.
    """
    base = {**PROGRAMS[0], "period_start_date": date(2026, 6, 1)}
    frames: list[bytes] = []
    for index in range(3):
        packed = protocol.pack_set_irrigation_program(
            index,
            {**base, "name": f"Program {index}"},
            today=date(2026, 10, 19),
            max_stations=2,
        )
        for chunk, frame in enumerate(packed):
            frames.append(bytes([frame[0], frame[1], 6 - chunk]) + bytes(frame[3:]))
    if header_phase is not None:
        cycle, period_length, synchro_day = header_phase
        forged: list[bytes] = []
        for frame in frames:
            if frame[3] == 0x10 and frame[2] == 4:  # program A header fragment
                header = bytearray(frame)
                header[8] = cycle
                header[10] = period_length
                header[11] = synchro_day
                frame = bytes(header)
            forged.append(frame)
        frames = forged
    return ProgramSnapshot.from_frames(tuple(frames))


@pytest.mark.asyncio
async def test_restore_rejects_real_snapshot_with_out_of_range_phase(
    hass: HomeAssistant, coordinator, mock_solem_client
) -> None:
    """A real captured snapshot whose header is out of range fails the guard.

    The backup program comes from a genuine ``ProgramSnapshot`` built from
    packed V5 frames, with program A's header carrying ``synchro_day ==
    period_length``. ``patch`` re-validates the captured header, so the guard
    fires while the restore plan is built: no journal entry, no write, and the
    protected backup is untouched.
    """
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    out_of_range = {
        0: {
            **PROGRAMS[0],
            "cycle": 4,
            "period_length": 3,
            "synchro_day": 3,
        }
    }
    assert await coordinator.program_backup.async_save_if_non_empty(out_of_range)

    before = _real_snapshot(header_phase=(4, 3, 3))
    assert before.programs[0]["cycle"] == 4
    assert before.programs[0]["period_length"] == 3
    assert before.programs[0]["synchro_day"] == 3
    mock_solem_client.get_program_snapshot = AsyncMock(return_value=before)
    mock_solem_client.write_program_frames = AsyncMock()
    coordinator.api = mock_solem_client

    with patch.object(
        coordinator.program_backup, "async_begin_restore", new=AsyncMock()
    ) as begin_restore:
        with pytest.raises(HomeAssistantError) as exc_info:
            await coordinator.restore_irrigation_programs()

    message = str(exc_info.value)
    assert "program A" in message
    assert "period_length 3" in message and "synchro_day 3" in message
    begin_restore.assert_not_awaited()
    mock_solem_client.write_program_frames.assert_not_awaited()
    assert coordinator.program_backup.pending is None
    assert coordinator.program_backup.programs == out_of_range
