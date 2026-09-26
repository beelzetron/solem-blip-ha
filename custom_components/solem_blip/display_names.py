"""Cached onboard station/program display names across restarts.

The coordinator keeps station names and program display names in memory
only, so after a restart every entity reverts to the default slot names
("Station 1", "Program A") until the first heavy metadata read completes
— one poll cycle, longer when the controller is asleep (issue #118).
This store persists the last successfully *observed* onboard names per
config entry and is restored in coordinator init, so offline startups
and the pre-read window show the last-known names.

The device always remains the source of truth: every successful
observation (a heavy metadata read, an irrigation-config read, or the
station-name editor's own verified readback via ``rename_station``)
refreshes the cache, so on-device renames propagate on the next cycle.

The cache only feeds friendly-name construction; it never touches
``name_by_user``, registry overrides, or entity identity slots.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_SCHEMA_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.display_names"


def _validated_names(raw: Any) -> dict[int, str]:
    """Return a sanitized {index: name} map, or {} for malformed data."""
    if not isinstance(raw, dict):
        return {}
    names: dict[int, str] = {}
    for key, value in raw.items():
        if not isinstance(key, (int, str)) or not isinstance(value, str):
            return {}
        try:
            index = int(key)
        except (TypeError, ValueError):
            return {}
        name = value.strip()
        if not name:
            return {}
        names[index] = name
    return names


class DisplayNamesStore:
    """Persist the last successfully observed onboard display names."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.store: Store[dict[str, Any]] = Store(
            hass, _SCHEMA_VERSION, f"{_STORAGE_KEY}.{entry_id}", private=True
        )
        self.station_names: dict[int, str] = {}
        self.program_names: dict[int, str] = {}

    async def async_load(self) -> None:
        """Load the cache, ignoring absent, corrupt, or mismatched payloads."""
        self.station_names = {}
        self.program_names = {}
        try:
            data = await self.store.async_load()
        except Exception:
            return
        if not isinstance(data, dict):
            return
        if data.get("schema_version") != _SCHEMA_VERSION:
            return
        self.station_names = _validated_names(data.get("station_names"))
        self.program_names = _validated_names(data.get("program_names"))

    async def async_save(
        self,
        *,
        station_names: dict[int, str] | None = None,
        program_names: dict[int, str] | None = None,
    ) -> None:
        """Merge observed names into the cache and persist it."""
        if station_names is not None:
            self.station_names = _validated_names(station_names)
        if program_names is not None:
            self.program_names = _validated_names(program_names)
        await self.store.async_save(
            {
                "schema_version": _SCHEMA_VERSION,
                "station_names": {
                    str(station): name
                    for station, name in self.station_names.items()
                },
                "program_names": {
                    str(index): name
                    for index, name in self.program_names.items()
                },
                "saved_at": dt_util.utcnow().isoformat(),
            }
        )
