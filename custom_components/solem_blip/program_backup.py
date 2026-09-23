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
        """Clear a pending restore only when a fresh read has a known outcome."""
        if self._pending is None:
            return True
        known_revisions = {
            self._pending.get("before_revision"),
            self._pending.get("expected_revision"),
        }
        if snapshot.revision not in known_revisions:
            return False
        self._snapshot = snapshot
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
