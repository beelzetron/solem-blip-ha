"""Persistent backup helpers for on-device irrigation programs."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from solem_blip_ble import IrrigationProgram

from .const import DOMAIN

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.program_backup"


class ProgramBackupStore:
    """Persist the last known non-empty irrigation program set."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store[dict[str, Any]](
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY}.{entry_id}",
        )
        self._programs: dict[int, IrrigationProgram] = {}

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

    async def async_save_if_non_empty(
        self, programs: dict[int, IrrigationProgram]
    ) -> bool:
        """Persist programs only when at least one slot contains a real schedule."""
        if not _has_scheduled_program(programs):
            return False
        self._programs = deepcopy(programs)
        await self._store.async_save(
            {
                "programs": {
                    str(index): _serialize_program(program)
                    for index, program in self._programs.items()
                }
            }
        )
        return True


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
