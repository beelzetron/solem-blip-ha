"""Device-derived station count tests (issue #122).

The coordinator derives the station width from the device's station-name
reads with the same UPWARD-ONLY invariant as solem-blip-ble 0.3.2b6:
the device always reports every configured slot, so the highest named
output is a lower bound on the physical width — a name read can raise
the width but never lower it. num_stations starts as the configured
knob (a trustworthy floor) and a repair notice is raised once when a
name read proves the configured value stale/too low.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.const import (
    CONTROLLER_MAC_ADDRESS,
    DOMAIN,
    NUM_STATIONS,
)
from custom_components.solem_blip.coordinator import SolemCoordinator
from custom_components.solem_blip.coordinator_polling import (
    derive_station_count_from_names,
    maybe_adopt_device_station_count,
)
from custom_components.solem_blip.issues import ISSUE_STATION_COUNT_MISMATCH

_DISPLAY_KEY = "solem_blip.display_names.{entry_id}"


@contextmanager
def _mock_hass_storage(data: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """In-memory storage backend for Stores created inside the block."""
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


def _make_coordinator(
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
        coordinator.async_update_all_sensors = AsyncMock(return_value=[])
        coordinator.publish_descriptor_update = MagicMock()  # type: ignore[method-assign]
        return coordinator


def _entry(
    num_stations: int | None = 2,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    data: dict[str, Any] = {
        CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"
    }
    if num_stations is not None:
        data[NUM_STATIONS] = num_stations
    return MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options=options or {},
        unique_id="AA:BB:CC:DD:EE:FF",
    )


def test_derive_station_count_from_names_snapshot_semantics() -> None:
    """Highest named output wins; empty names fall back to max reported key."""
    assert derive_station_count_from_names({1: "A", 2: "B", 3: "C"}) == 3
    assert derive_station_count_from_names({1: "A", 2: "", 3: "C"}) == 3
    # All names empty: fall back to the highest reported output number.
    assert derive_station_count_from_names({1: "", 2: ""}) == 2
    # Whitespace-only names are unnamed.
    assert derive_station_count_from_names({1: "  ", 2: "   "}) == 2
    assert derive_station_count_from_names({}) is None


def test_maybe_adopt_is_upward_only() -> None:
    """Growth adopts on a single read; a narrower read never shrinks."""
    coordinator = MagicMock()
    coordinator.api = MagicMock()
    coordinator.api.station_count = 2
    coordinator._client_initial_station_count = 2
    coordinator.device_station_count = 2

    # A single wider read adopts immediately (rename names a higher slot).
    maybe_adopt_device_station_count(coordinator, {1: "A", 2: "B", 3: "C"})
    coordinator.adopt_device_station_count.assert_called_once_with(3)

    # A narrower read (unnamed higher slots / partial dict) keeps the
    # adopted width: max(3, 1) — never a downward adoption.
    coordinator.adopt_device_station_count.reset_mock()
    coordinator.device_station_count = 3
    maybe_adopt_device_station_count(coordinator, {1: "A"})
    coordinator.adopt_device_station_count.assert_called_once_with(3)

    # An equal read is a no-op through the same max path.
    maybe_adopt_device_station_count(coordinator, {1: "A", 2: "B", 3: "C"})
    coordinator.adopt_device_station_count.assert_called_with(3)


def test_maybe_adopt_ignores_unrefreshed_client_attribute() -> None:
    """A client station_count equal to the constructor width carries no data."""
    coordinator = MagicMock()
    coordinator.api = MagicMock()
    coordinator.api.station_count = 2
    coordinator._client_initial_station_count = 2
    coordinator.device_station_count = 2

    # The stale constructor mirror must not be passed through: the dict
    # read path derives the count instead.
    maybe_adopt_device_station_count(coordinator, {1: "A"})
    coordinator.adopt_device_station_count.assert_called_once_with(2)

    # A refreshed client attribute (differs from the constructor width)
    # is authoritative even when the dict read saw fewer outputs.
    coordinator2 = MagicMock()
    coordinator2.api = MagicMock()
    coordinator2.api.station_count = 5
    coordinator2._client_initial_station_count = 2
    maybe_adopt_device_station_count(coordinator2, {1: "A"})
    coordinator2.adopt_device_station_count.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_device_station_count_adopted_after_name_read(
    hass: HomeAssistant,
) -> None:
    """A single successful name read adopts the device count immediately."""
    entry = _entry(num_stations=2)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 2
    # A single read proving a wider width than configured.
    client.get_station_names = AsyncMock(
        return_value={1: "Zone 1", 2: "Zone 2", 3: "Zone 3"}
    )
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    assert coordinator.device_station_count == 2
    assert coordinator.num_stations == 2

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 3
    assert coordinator.num_stations == 3
    assert len(coordinator.stations) == 3


@pytest.mark.asyncio
async def test_device_station_count_never_shrinks_below_adopted_value(
    hass: HomeAssistant,
) -> None:
    """A name read whose derived count is lower keeps the existing width."""
    entry = _entry(num_stations=4)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 4
    # Stations 3-4 exist physically but carry no onboard name: the
    # derived count (2) is below the adopted width (4).
    client.get_station_names = AsyncMock(return_value={1: "A", 2: "B"})
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()
        assert coordinator.device_station_count == 4
        # Repeated agreeing reads keep the width stable.
        await coordinator._fetch_device_metadata()
        assert coordinator.device_station_count == 4
        assert coordinator.num_stations == 4

    assert len(coordinator.stations) == 4


@pytest.mark.asyncio
async def test_growth_adopts_immediately_on_single_read(
    hass: HomeAssistant,
) -> None:
    """A wider read grows the width on the first sight, no second read needed."""
    entry = _entry(num_stations=2)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 4
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        # First read: partial dict (station 2 missing). The derived count
        # (1) is below the configured width: no shrink, trigger keeps firing.
        client.get_station_names = AsyncMock(return_value={1: "A"})
        await coordinator._fetch_device_metadata()
        assert coordinator.device_station_count == 2
        assert coordinator.num_stations == 2

        # Next read proves a higher slot than configured (e.g. the user
        # named station 3 onboard): ONE read grows the width immediately.
        client.get_station_names = AsyncMock(
            return_value={1: "A", 2: "B", 3: "New zone"}
        )
        coordinator._station_names_retry_after = 0.0
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 3
    assert coordinator.num_stations == 3
    assert len(coordinator.stations) == 3
    # The name covering the new width passes the merge filter.
    assert coordinator.station_names[3] == "New zone"


@pytest.mark.asyncio
async def test_retry_trigger_keeps_firing_for_unnamed_higher_stations(
    hass: HomeAssistant,
) -> None:
    """A narrower device re-reads names every metadata cycle.

    With upward-only semantics the trigger
    (``len(station_names) < num_stations``) can never be satisfied by a
    device whose highest named output is below the configured width: the
    read-every-poll behavior is the deliberate tradeoff (correct width
    over read frequency), documented in
    ``maybe_adopt_device_station_count``.
    """
    entry = _entry(num_stations=4)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 4
    client.get_station_names = AsyncMock(return_value={1: "A", 2: "B"})
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()
        assert coordinator.device_station_count == 4
        assert coordinator.num_stations == 4

        # The cached names (2) stay below num_stations (4): the trigger
        # keeps firing on every cycle.
        client.get_station_names.reset_mock()
        await coordinator._fetch_device_metadata()
        client.get_station_names.assert_awaited_once()
        await coordinator._fetch_device_metadata()
        assert client.get_station_names.await_count == 2

    assert coordinator.num_stations == 4


@pytest.mark.asyncio
async def test_retry_trigger_stops_when_names_cover_width(
    hass: HomeAssistant,
) -> None:
    """A device whose names cover the width stops re-reading (no infinite loop)."""
    entry = _entry(num_stations=2)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 2
    client.get_station_names = AsyncMock(return_value={1: "A", 2: "B"})
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()
        assert coordinator.num_stations == 2

        client.get_station_names.reset_mock()
        await coordinator._fetch_device_metadata()

    client.get_station_names.assert_not_awaited()
    assert coordinator.station_names == {1: "A", 2: "B"}


@pytest.mark.asyncio
async def test_num_stations_property_follows_device_count(
    hass: HomeAssistant,
) -> None:
    """The property returns the upward-only device-derived width."""
    entry = _entry(num_stations=4)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 4
    client.get_station_names = AsyncMock(return_value={1: "A", 2: "B"})
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    assert coordinator.num_stations == 4
    assert coordinator.adopt_device_station_count(5) is True
    assert coordinator.device_station_count == 5
    assert coordinator.num_stations == 5
    assert len(coordinator.stations) == 5

    # No-op when the device count is unchanged.
    assert coordinator.adopt_device_station_count(5) is False
    # None (mock mode / no client signal) keeps the current value.
    assert coordinator.adopt_device_station_count(None) is False
    # A narrower count can never shrink the adopted width.
    assert coordinator.adopt_device_station_count(3) is False
    assert coordinator.num_stations == 5


@pytest.mark.asyncio
async def test_repair_issue_created_on_disagreement(hass: HomeAssistant) -> None:
    """A device proving more stations than configured raises the notice."""
    from homeassistant.helpers import issue_registry as ir

    entry = _entry(num_stations=2)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 2
    client.get_station_names = AsyncMock(
        return_value={1: "A", 2: "B", 3: "C", 4: "D"}
    )
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.coordinator.ir.async_create_issue"
    ) as create_issue, _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 4
    create_issue.assert_called_once()
    kwargs = create_issue.call_args.kwargs
    assert kwargs["translation_key"] == ISSUE_STATION_COUNT_MISMATCH
    assert kwargs["translation_placeholders"] == {
        "configured": "2",
        "reported": "4",
    }
    # Only once per entry: a repeated adoption does not raise again.
    with patch(
        "custom_components.solem_blip.coordinator.ir.async_create_issue"
    ) as create_issue2:
        coordinator.adopt_device_station_count(5)
        create_issue2.assert_not_called()


@pytest.mark.asyncio
async def test_no_repair_issue_when_counts_agree(hass: HomeAssistant) -> None:
    """Agreement between config knob and device count stays silent."""
    entry = _entry(num_stations=2)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 2
    client.get_station_names = AsyncMock(return_value={1: "A", 2: "B"})
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    with patch(
        "custom_components.solem_blip.coordinator.ir.async_create_issue"
    ) as create_issue, _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 2
    create_issue.assert_not_called()


@pytest.mark.asyncio
async def test_no_repair_issue_when_num_stations_not_configured(
    hass: HomeAssistant,
) -> None:
    """The default (no explicit num_stations) never raises the notice."""
    entry = _entry(num_stations=None)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 2
    client.get_station_names = AsyncMock(return_value={1: "A", 2: "B", 3: "C"})
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    with patch(
        "custom_components.solem_blip.coordinator.ir.async_create_issue"
    ) as create_issue, _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 3
    create_issue.assert_not_called()


@pytest.mark.asyncio
async def test_polling_filter_uses_grown_device_count(
    hass: HomeAssistant,
) -> None:
    """Names beyond the configured knob are kept once the device proves them."""
    entry = _entry(num_stations=2)
    client = MagicMock()
    client.mock = True
    client.max_station_num = 4
    client.get_station_names = AsyncMock(
        return_value={1: "A", 2: "B", 3: "C", 4: "D"}
    )
    client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 4
    assert coordinator.num_stations == 4
    # All four names pass the width filter, not just the configured two.
    assert coordinator.station_names == {1: "A", 2: "B", 3: "C", 4: "D"}
    assert len(coordinator.stations) == 4


@pytest.mark.asyncio
async def test_station_names_partial_reads_merge_unchanged(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A partial dict read never shrinks the width before retries merge."""
    responses = [{1: "Zone 1"}, {2: "Zone 2"}]

    async def partial_names() -> dict[int, str]:
        return responses.pop(0)

    mock_solem_client.get_station_names = partial_names

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        await coordinator._fetch_device_metadata()
        coordinator._station_names_retry_after = 0.0
        await coordinator._fetch_device_metadata()

    assert coordinator.station_names == {1: "Zone 1", 2: "Zone 2"}
    assert coordinator.device_station_count == 2


@pytest.mark.asyncio
async def test_pin_is_solem_blip_ble_0_3_2_b6() -> None:
    """The manifest and pyproject pin solem-blip-ble==0.3.2b6."""
    import pathlib

    import tomllib

    root = pathlib.Path(__file__).parent.parent
    manifest = json.loads(
        (root / "custom_components/solem_blip/manifest.json").read_text()
    )
    assert "solem-blip-ble==0.3.2b6" in manifest["requirements"]

    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    deps = pyproject["project"]["dependencies"]
    assert "solem-blip-ble==0.3.2b6" in deps
