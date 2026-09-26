"""Display-name cache tests (issue #118).

The DisplayNamesStore must survive restarts so entities show the
last-known onboard station/program names immediately (no "Station N"/
"Program X" window), stay entry-scoped and tolerant of corrupt payloads,
and always converge to the device as source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.coordinator import SolemCoordinator
from custom_components.solem_blip.coordinator_polling import fetch_irrigation_config
from custom_components.solem_blip.display_names import DisplayNamesStore
from tests.conftest import MOCK_IRRIGATION_PROGRAMS

_DISPLAY_KEY = "solem_blip.display_names.{entry_id}"


@contextmanager
def _mock_hass_storage(data: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """In-memory storage backend for Stores created inside the block.

    A minimal stand-in for common.mock_storage (whose autospec patching is
    incompatible with the pytest-homeassistant hass fixture in this
    environment): loads come from ``data``, writes are JSON round-tripped
    back into it.
    """
    if data is None:
        data = {}
    orig_load = Store._async_load
    orig_write = Store._async_write_data

    async def mock_async_load(self: Store) -> Any:
        if self._data is not None:
            return await orig_load(self)
        mock_data = data.get(self.key)
        if mock_data is None:
            return None
        self._data = dict(mock_data)
        return await orig_load(self)

    async def mock_write_data(self: Store, data_to_write: dict[str, Any]) -> None:
        data[self.key] = json.loads(json.dumps(data_to_write))

    with (
        patch.object(Store, "_async_load", mock_async_load),
        patch.object(Store, "_async_write_data", mock_write_data),
    ):
        yield data


def _payload(
    *,
    station_names: dict[str, Any] | None = None,
    program_names: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "station_names": station_names or {},
        "program_names": program_names or {},
        "saved_at": "2026-09-26T00:00:00+00:00",
    }


async def _make_coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> SolemCoordinator:
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
        return_value=MagicMock(address="AA:BB:CC:DD:EE:FF", name="Solem BL-IP"),
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        return coordinator


async def test_restore_on_async_init_seeds_names_without_ble(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A restart with the controller unreachable shows cached names."""
    mock_solem_client.mock = False
    # Never touch BLE: the names must come from the cache alone.
    mock_solem_client.get_status = AsyncMock(side_effect=OSError("offline"))
    mock_solem_client.get_station_names = AsyncMock(side_effect=OSError("offline"))
    mock_solem_client.get_irrigation_config = AsyncMock(
        side_effect=OSError("offline")
    )

    storage: dict[str, Any] = {
        _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id): {
            "version": 1,
            "data": _payload(
                station_names={"1": "Orto", "2": "Giardino"},
                program_names={"0": "Prato", "2": "Fiori"},
            ),
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )

    assert coordinator.station_names == {1: "Orto", 2: "Giardino"}
    assert coordinator.program_names == {0: "Prato", 2: "Fiori"}
    assert coordinator._station_name(1) == "Orto"
    assert coordinator._station_name(2) == "Giardino"
    assert coordinator._program_display_name(0) == "Prato"
    assert coordinator._program_display_name(1) == "Program B"
    assert coordinator._program_display_name(2) == "Fiori"
    mock_solem_client.get_station_names.assert_not_awaited()


async def test_successful_station_names_read_persists_cache(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A successful metadata read refreshes the persisted station names."""
    storage: dict[str, Any] = {}
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
        assert coordinator.display_names.station_names == {}

        await coordinator._fetch_device_metadata()

    assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}
    key = _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id)
    assert storage[key]["data"]["station_names"] == {"1": "Zone 1", "2": "Zone 2"}


async def test_successful_station_names_read_refreshes_after_restore(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A seeded full cache still forces one refresh to pick up renames."""
    storage: dict[str, Any] = {
        _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id): {
            "version": 1,
            "data": _payload(station_names={"1": "Orto", "2": "Giardino"}),
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
        assert coordinator.station_names == {1: "Orto", 2: "Giardino"}
        assert coordinator._station_names_restored

        await coordinator._fetch_device_metadata()

    assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}
    assert not coordinator._station_names_restored
    key = _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id)
    assert storage[key]["data"]["station_names"] == {"1": "Zone 1", "2": "Zone 2"}


async def test_restored_cache_does_not_skip_first_read(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """The count check alone would suppress the post-restart refresh."""
    storage: dict[str, Any] = {
        _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id): {
            "version": 1,
            "data": _payload(station_names={"1": "Orto", "2": "Giardino"}),
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
        # Simulate the pre-fix gate: full cache, no restore flag.
        coordinator._station_names_restored = False

        await coordinator._fetch_device_metadata()

    mock_solem_client.get_station_names.assert_not_awaited()
    assert coordinator.station_names == {1: "Orto", 2: "Giardino"}


async def test_successful_irrigation_config_read_persists_program_names(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A successful config read caches the on-device program names."""
    storage: dict[str, Any] = {}
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
        assert coordinator.display_names.program_names == {}

        assert await fetch_irrigation_config(coordinator) is True

    key = _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id)
    assert storage[key]["data"]["program_names"] == {
        "0": "Programma A",
        "1": "Programma B",
        "2": "Programma C",
    }


async def test_rename_station_refreshes_cache_to_verified_snapshot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """The editor's verified readback updates the cache in the same flow."""
    from solem_blip_ble.station_names import StationNameSnapshot

    def _snapshot(num: int = 2) -> StationNameSnapshot:
        return StationNameSnapshot(
            {i: f"Station {i}".encode().ljust(32, b"\0") for i in range(1, num + 1)}
        )

    mock_solem_client.max_station_num = 2
    mock_solem_client.get_status = AsyncMock(
        return_value={"is_watering": False, "controller_state": "Off"}
    )
    mock_solem_client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    mock_solem_client.get_station_name_snapshot = AsyncMock(
        return_value=_snapshot()
    )

    async def fake_write(station, name, expected, *, before):
        return expected

    mock_solem_client.write_station_name = AsyncMock(side_effect=fake_write)

    storage: dict[str, Any] = {}
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
        manager = coordinator.station_name_manager
        await manager.refresh()
        snapshot = manager.snapshot
        assert snapshot is not None

        await coordinator.rename_station(1, "Orto", snapshot.revision)

    assert coordinator.station_names[1] == "Orto"
    key = _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id)
    assert storage[key]["data"]["station_names"] == {"1": "Orto", "2": "Station 2"}
    assert "Station 1" not in storage[key]["data"]["station_names"].values()


async def test_set_irrigation_program_refreshes_program_name_cache(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A program rename through the editor updates the cache immediately."""
    renamed_echo = deepcopy(MOCK_IRRIGATION_PROGRAMS)
    renamed_echo[0]["name"] = "Orto pranzo"
    mock_solem_client.set_irrigation_program = AsyncMock(return_value=renamed_echo)

    storage: dict[str, Any] = {}
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
        renamed = deepcopy(MOCK_IRRIGATION_PROGRAMS)
        renamed[0]["name"] = "Orto pranzo"

        await coordinator.set_irrigation_program(0, renamed[0])

    key = _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id)
    assert storage[key]["data"]["program_names"]["0"] == "Orto pranzo"


async def test_corrupt_cache_payload_is_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Wrong schema or malformed payloads fall back to defaults, setup proceeds."""
    storage: dict[str, Any] = {
        # Mismatched schema version: ignored entirely.
        _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id): {
            "version": 1,
            "data": {
                "schema_version": 99,
                "station_names": {"1": "Orto"},
                "program_names": {"0": "Prato"},
            },
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
    assert coordinator.station_names == {}
    assert coordinator.program_names == {}
    assert coordinator._station_name(1) == "Station 1"
    assert coordinator._program_display_name(0) == "Program A"


async def test_non_dict_cache_payload_is_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A non-dict cached payload is treated as absent."""
    storage: dict[str, Any] = {
        _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id): {
            "version": 1,
            "data": ["not", "a", "dict"],
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
    assert coordinator.station_names == {}
    assert coordinator.program_names == {}
    assert coordinator._station_name(1) == "Station 1"


async def test_malformed_name_values_are_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Non-string names or unparseable keys are treated as an absent cache."""
    storage: dict[str, Any] = {
        _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id): {
            "version": 1,
            "data": _payload(
                station_names={"1": 42, "2": "Giardino"},
                program_names={"x": "Prato"},
            ),
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
    assert coordinator.station_names == {}
    assert coordinator.program_names == {}


async def test_store_load_failure_does_not_break_init(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A display-names store that fails to load must not break async_init."""
    original_load = Store.async_load

    async def failing_load(self: Store, *args: Any) -> Any:
        if self.key.startswith("solem_blip.display_names"):
            raise OSError("disk error")
        return await original_load(self, *args)

    with patch.object(Store, "async_load", failing_load):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )

    assert coordinator.station_names == {}
    assert coordinator.program_names == {}
    assert coordinator._station_name(1) == "Station 1"
    assert coordinator._program_display_name(0) == "Program A"


async def test_program_display_name_precedence(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Live program name wins over cached; cached wins over slot fallback."""
    storage: dict[str, Any] = {
        _DISPLAY_KEY.format(entry_id=mock_config_entry.entry_id): {
            "version": 1,
            "data": _payload(program_names={"0": "Cache A", "1": "Cache B"}),
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )

        # Slot fallback only: no live program, no cached name for index 2.
        assert coordinator._program_display_name(2) == "Program C"
        # Cached name wins over the slot fallback.
        assert coordinator._program_display_name(1) == "Cache B"
        # No live program for index 0 yet either.
        assert coordinator._program_display_name(0) == "Cache A"

        # A live read with a name always wins over the cache.
        coordinator.irrigation_programs = {0: dict(MOCK_IRRIGATION_PROGRAMS[0])}
        coordinator.irrigation_programs[0]["name"] = "Live A"
        assert coordinator._program_display_name(0) == "Live A"
        # A blank live name falls through to the cache, not the slot.
        coordinator.irrigation_programs = {0: dict(MOCK_IRRIGATION_PROGRAMS[0])}
        coordinator.irrigation_programs[0]["name"] = "   "
        assert coordinator._program_display_name(0) == "Cache A"


async def test_cache_is_entry_scoped(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Names cached for one entry do not leak into another entry."""
    storage: dict[str, Any] = {
        "solem_blip.display_names.other-entry": {
            "version": 1,
            "data": _payload(station_names={"1": "Other"}),
        }
    }
    with _mock_hass_storage(storage):
        coordinator = await _make_coordinator(
            hass, mock_config_entry, mock_solem_client
        )
    assert coordinator.station_names == {}
    assert coordinator.display_names.station_names == {}
