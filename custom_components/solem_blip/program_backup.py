"""Persistent backup helpers for on-device irrigation programs."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from solem_blip_ble import IrrigationProgram
from solem_blip_ble.snapshot import InvalidSnapshot, ProgramSnapshot

from .const import DOMAIN

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.program_backup"


class ProgramBackupStore:
    """Persist a protected non-empty irrigation program snapshot."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store[dict[str, Any]](
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY}.{entry_id}",
        )
        self._programs: dict[int, IrrigationProgram] = {}
        self._snapshot: ProgramSnapshot | None = None
        self._pending: dict[str, Any] | None = None

    @property
    def snapshot(self) -> ProgramSnapshot | None:
        """Return the confirmed complete raw snapshot, when available."""
        return self._snapshot

    @property
    def pending(self) -> dict[str, Any] | None:
        """Return an unconfirmed restore journal entry."""
        return deepcopy(self._pending)

    @property
    def programs(self) -> dict[int, IrrigationProgram]:
        """Return a defensive copy of the current backup."""
        return deepcopy(self._programs)

    async def async_load(self) -> None:
        """Load a persisted backup, if present."""
        data = await self._store.async_load()
        if not data:
            return
        raw_programs = data.get("programs", {})
        self._programs = {
            int(index): _deserialize_program(program)
            for index, program in raw_programs.items()
        }
        self._pending = data.get("pending")
        raw_frames = data.get("frames", [])
        if raw_frames:
            try:
                self._snapshot = ProgramSnapshot.from_frames(
                    tuple(bytes.fromhex(frame) for frame in raw_frames)
                )
            except (ValueError, InvalidSnapshot):
                self._snapshot = None

        # beta.12 could persist a mixed store after reconciling an unchanged
        # controller: the logical A/B/C backup stayed protected while the raw
        # 84-frame snapshot was replaced by the degraded live state. Repair
        # that inconsistency locally. This never talks to BLE and preserves
        # all additional V5 slots byte-for-byte from the stored snapshot.
        if (
            self._pending is None
            and self._snapshot is not None
            and self._programs
            and self._snapshot_programs_differ()
        ):
            repaired = self._snapshot
            try:
                for program_index, program in sorted(self._programs.items()):
                    # Patch only logical programs that actually differ from
                    # the raw snapshot. Matching program blocks must remain
                    # byte-for-byte untouched during the beta.12 migration.
                    if repaired.programs.get(program_index) == program:
                        continue
                    changes = _program_changes(program)
                    _, repaired = repaired.patch(
                        program_index,
                        changes,
                        len(program.get("station_durations", [])),
                    )
            except (KeyError, ValueError, InvalidSnapshot):
                # Keep the logical backup authoritative if an old snapshot
                # cannot be repaired safely.
                return
            self._snapshot = repaired
            await self._async_save()

    def _snapshot_programs_differ(self) -> bool:
        """Return whether protected logical A/B/C differ from raw frames."""
        if self._snapshot is None:
            return False
        return any(
            self._snapshot.programs.get(index) != program
            for index, program in self._programs.items()
        )

    async def async_save_if_non_empty(
        self, programs: dict[int, IrrigationProgram]
    ) -> bool:
        """Persist the first useful snapshot without replacing an existing backup.

        Controller reads are observations, not proof that a changed schedule is
        a better restore point. Once a useful backup exists, normal polling and
        schedule writes must not silently replace it.
        """
        if self._programs or not _has_scheduled_program(programs):
            return False
        self._programs = deepcopy(programs)
        await self._async_save()
        return True

    async def async_begin_restore(
        self, before: ProgramSnapshot, expected: ProgramSnapshot
    ) -> None:
        """Durably journal an intended restore before any BLE mutation."""
        self._pending = {
            "before_revision": before.revision,
            "expected_revision": expected.revision,
            "before_frames": [frame.hex() for frame in before.frames],
            "expected_frames": [frame.hex() for frame in expected.frames],
        }
        await self._async_save()

    async def async_abort_restore(self) -> None:
        """Clear a journal when the BLE layer confirms no mutation was attempted."""
        self._pending = None
        await self._async_save()

    async def async_finish_restore(self, snapshot: ProgramSnapshot) -> None:
        """Persist a verified raw snapshot and clear the restore journal."""
        self._snapshot = snapshot
        self._pending = None
        await self._async_save()

    async def async_reconcile(self, snapshot: ProgramSnapshot) -> bool:
        """Clear a pending restore only when a fresh read has a known outcome.

        A read matching the expected revision confirms the mutation and becomes
        the protected raw snapshot. A read matching the before revision proves
        that the controller stayed at its pre-restore state: clear the journal,
        but never replace a protected snapshot with that potentially degraded
        controller state. If no protected raw snapshot exists yet, recover the
        intended protected snapshot from the journal's expected frames.
        """
        if self._pending is None:
            return True

        before_revision = self._pending.get("before_revision")
        expected_revision = self._pending.get("expected_revision")

        if snapshot.revision == expected_revision:
            self._snapshot = snapshot
            self._pending = None
            await self._async_save()
            return True

        if snapshot.revision != before_revision:
            return False

        if self._snapshot is None:
            raw_expected_frames = self._pending.get("expected_frames", [])
            if raw_expected_frames:
                try:
                    self._snapshot = ProgramSnapshot.from_frames(
                        tuple(bytes.fromhex(frame) for frame in raw_expected_frames)
                    )
                except (ValueError, InvalidSnapshot):
                    # The legacy program backup remains authoritative even when
                    # an old/broken journal cannot reconstruct its raw snapshot.
                    self._snapshot = None

        self._pending = None
        await self._async_save()
        return True

    async def _async_save(self) -> None:
        """Persist legacy programs, raw snapshot and pending journal together."""
        await self._store.async_save(
            {
                "programs": {
                    str(index): _serialize_program(program)
                    for index, program in self._programs.items()
                },
                "frames": (
                    [frame.hex() for frame in self._snapshot.frames]
                    if self._snapshot is not None
                    else []
                ),
                "pending": self._pending,
            }
        )


def _program_changes(program: IrrigationProgram) -> dict[str, Any]:
    """Return snapshot patch fields for one protected logical program."""
    changes: dict[str, Any] = {
        "name": program["name"],
        "inter_station_delay": program["inter_station_delay"],
        "water_budget": program["water_budget"],
        "cycle": program["cycle"],
        "week_days": program["week_days"],
        "period_length": program["period_length"],
        "synchro_day": program["synchro_day"],
        "start_times": list(program["start_times"]),
        "station_durations": {
            station: seconds
            for station, seconds in enumerate(program["station_durations"], start=1)
        },
    }
    if program.get("period_start_date") is not None:
        changes["period_start_date"] = program["period_start_date"]
    return changes


def _has_scheduled_program(programs: dict[int, IrrigationProgram]) -> bool:
    """Return whether at least one program has a start and a station duration."""
    return any(
        any(start is not None for start in program.get("start_times", []))
        and any(duration > 0 for duration in program.get("station_durations", []))
        for program in programs.values()
    )


def _serialize_program(program: IrrigationProgram) -> dict[str, Any]:
    """Convert an irrigation program to JSON-safe storage data."""
    data = dict(program)
    period_start_date = data.get("period_start_date")
    if isinstance(period_start_date, date):
        data["period_start_date"] = period_start_date.isoformat()
    return data


def _deserialize_program(data: dict[str, Any]) -> IrrigationProgram:
    """Restore an irrigation program from JSON-safe storage data."""
    restored = dict(data)
    period_start_date = restored.get("period_start_date")
    if isinstance(period_start_date, str):
        restored["period_start_date"] = date.fromisoformat(period_start_date)
    return cast(IrrigationProgram, restored)
