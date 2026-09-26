"""Durable onboard station renames with an explicit reconciliation journal.

Ported from the hardware-validated ThomasHFWright fork design
(solem-blip-ha PR #1, ``station_names.py`` + ``StationNameManager``),
adapted to this integration and the separate ``solem_blip_ble`` library
(``StationNameSnapshot`` / ``write_station_name``, see
beelzetron/solem-blip-ble#55).

Safety model (mirrors the program backup/restore pattern):

- every write is preceded by a fresh complete name snapshot preflight on
  one short subscribed connection, and a stale draft is rejected;
- only the selected output is written; the library matches per-part
  acknowledgements and verifies the full names by readback on the same
  connection;
- an interrupted save leaves an explicit pending journal: no auto
  replay, and further name writes are blocked until a fresh read
  reconciles the controller state;
- writes are refused while the controller is watering and on firmware
  other than 5.x;
- UTF-8 byte limits are enforced up front, before any connection.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from solem_blip_ble import protocol
from solem_blip_ble.exceptions import InvalidSnapshot, StaleProgram, UncertainWrite
from solem_blip_ble.station_names import StationNameSnapshot

from .const import DOMAIN

_STORAGE_VERSION = 1


class StationNameManager:
    """Never replay a name write whose outcome is uncertain."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        api: Any,
    ) -> None:
        self.api = api
        self.store: Store[dict[str, Any]] = Store(
            hass, _STORAGE_VERSION, f"{DOMAIN}.station_names.{entry_id}", private=True
        )
        self.snapshot: StationNameSnapshot | None = None
        self.pending: dict[str, Any] | None = None
        self.last_write: str | None = None

    async def async_load(self) -> None:
        """Load the persisted journal, if present."""
        data = await self.store.async_load() or {}
        self.pending = data.get("pending")
        self.last_write = data.get("last_write")

    async def _save(self) -> None:
        await self.store.async_save(
            {"pending": self.pending, "last_write": self.last_write}
        )

    async def refresh(self, *, accept_current: bool = False) -> StationNameSnapshot:
        """Read a fresh complete snapshot and reconcile a pending journal.

        ``accept_current`` (or a controller that already matches either
        recorded revision) clears the pending journal explicitly; the
        journal is never replayed automatically.
        """
        snapshot: StationNameSnapshot = await self.api.get_station_name_snapshot()
        self.snapshot = snapshot
        if self.pending and (
            accept_current
            or self.snapshot.revision
            in (self.pending["before_revision"], self.pending["expected_revision"])
        ):
            pending = self.pending
            self.pending = None
            try:
                await self._save()
            except (Exception, asyncio.CancelledError):
                self.pending = pending
                raise
        return self.snapshot

    async def update(
        self, station: int, name: str, revision: str
    ) -> StationNameSnapshot:
        """Rename one output after a full safety preflight.

        Raises ``ValueError`` for invalid input before touching the
        controller, ``InvalidSnapshot`` when the controller is busy or
        runs unsupported firmware, ``StaleProgram`` when the names
        changed since the draft was opened, and ``UncertainWrite`` (or
        any transport error) with the journal persisted when the write
        outcome is unknown.
        """
        # Validate up front: pack also enforces the 32 UTF-8 byte limit.
        max_stations = self.api.max_station_num
        protocol.pack_station_name(station, name, max_stations)
        if self.pending:
            raise UncertainWrite(
                "Review the current station names before saving again"
            )
        status = await self.api.get_status()
        if (
            status.get("is_watering") is not False
            or status.get("controller_state") not in ("On", "Off")
        ):
            raise InvalidSnapshot("Wait until the controller reports idle")
        firmware = await self.api.get_firmware_version()
        if firmware["major"] != 5:
            raise InvalidSnapshot(
                "Onboard station renaming requires BL-IP firmware 5.x"
            )
        before_raw = await self.api.get_station_name_snapshot()
        before: StationNameSnapshot = before_raw
        self.snapshot = before
        if before.revision != revision:
            raise StaleProgram("Station names changed; reopen the editor")
        if before.names.get(station) == name:
            return before
        expected = before.renamed(station, name, max_stations)
        self.pending = {
            "before_revision": before.revision,
            "expected_revision": expected.revision,
            "before_names": {
                str(k): v.hex() for k, v in before.raw_names.items()
            },
            "expected_names": {
                str(k): v.hex() for k, v in expected.raw_names.items()
            },
        }
        await self._save()
        journal = self.pending
        try:
            snapshot = await self.api.write_station_name(
                station, name, expected, before=before
            )
            verified: StationNameSnapshot = snapshot
            self.snapshot = verified
            self.last_write = datetime.now(timezone.utc).isoformat()
            self.pending = None
            await self._save()
            return verified
        except (Exception, asyncio.CancelledError):
            self.pending = journal
            raise
