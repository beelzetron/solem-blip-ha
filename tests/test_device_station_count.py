"""Device-derived station count tests (issue #122).

The coordinator adopts the device width from the VALIDATED station-name
snapshot with the same UPWARD-ONLY invariant as solem-blip-ble: the device
always reports every configured slot, so snapshot.station_count (highest
named output) is a lower bound on the physical width — an adoption can
raise the width but never lower it. num_stations starts as the configured
knob (a trustworthy floor) and a repair notice is raised once when a
snapshot read proves the configured value stale/too low.

D3 acceptance: the growth tests run against a filter-enforcing fake that
models the REAL library behavior — ``get_station_names()`` drops name
fragments above the client's constructor width (which would make polling
growth dead code), while ``get_station_name_snapshot()`` sees every
output. Growth must be demonstrable through the snapshot path.
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
    _station_names_trigger_active,
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


class _FilteringClient:
    """Fake mirroring the REAL solem-blip-ble width behavior (D3 acceptance).

    ``get_station_names`` (the plain dict read) drops every fragment above
    the client's constructor width ``max_station_num`` — exactly what
    client_v2 does (``if not 1 <= parsed["station"] <= self.max_station_num:
    return``) and why growth-via-polling was dead code on real hardware.

    ``get_station_name_snapshot`` (the validated read) sees ALL on-device
    names and raises ``client.station_count`` upward-only from the highest
    named output, like ``_read_station_names_on_connection`` in 0.3.2b7.
    """

    def __init__(
        self,
        max_station_num: int,
        on_device_names: dict[int, str],
    ) -> None:
        self.max_station_num = max_station_num
        self.on_device_names = dict(on_device_names)
        self.mock = True
        self.station_count = max_station_num
        self.get_station_names = AsyncMock(side_effect=self._dict_read)
        self.get_station_name_snapshot = AsyncMock(side_effect=self._snapshot_read)
        self.get_firmware_version = AsyncMock(
            return_value={"major": 5, "raw_hex": "5.1.5"}
        )

    def _dict_read(self) -> dict[int, str]:
        return {
            station: name
            for station, name in self.on_device_names.items()
            if station <= self.max_station_num
        }

    def _snapshot_read(self) -> SimpleSnapshot:
        named = [
            station for station, name in self.on_device_names.items() if name.strip()
        ]
        count = max(
            named or self.on_device_names.keys(), default=self.max_station_num
        )
        adopted = count if named else min(count, self.max_station_num)
        self.station_count = max(self.station_count, adopted)
        return SimpleSnapshot(dict(self.on_device_names), count)


class SimpleSnapshot:
    """Minimal StationNameSnapshot stand-in: names + station_count."""

    def __init__(self, names: dict[int, str], station_count: int) -> None:
        self.names = names
        self.station_count = station_count


def _make_coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: Any,
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


def test_station_names_trigger_active() -> None:
    """The re-read trigger fires while cached names sit below the width."""
    coordinator = MagicMock()
    coordinator.station_names = {1: "A", 2: "B"}
    coordinator.num_stations = 4
    assert _station_names_trigger_active(coordinator) is True
    coordinator.num_stations = 2
    assert _station_names_trigger_active(coordinator) is False


@pytest.mark.asyncio
async def test_device_station_count_adopted_after_name_read(
    hass: HomeAssistant,
) -> None:
    """A single successful snapshot read adopts the device count immediately."""
    entry = _entry(num_stations=2)
    # The device physically has 3 named stations; the client was built at
    # the configured floor (2), so the dict read would filter station 3.
    client = _FilteringClient(2, {1: "Zone 1", 2: "Zone 2", 3: "Zone 3"})
    coordinator = _make_coordinator(hass, entry, client)

    assert coordinator.device_station_count == 2
    assert coordinator.num_stations == 2

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 3
    assert coordinator.num_stations == 3
    assert len(coordinator.stations) == 3
    # The snapshot path — not the fragment-filtered dict read — proved it.
    client.get_station_names.assert_not_awaited()
    client.get_station_name_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_growth_through_filter_enforcing_fake(hass: HomeAssistant) -> None:
    """D3 acceptance: growth works despite the dict read's width filter.

    The client is constructed at the configured floor (max_station_num=2)
    and its ``get_station_names`` filters fragments above that width — the
    real library behavior that made growth-via-polling dead code. Only the
    snapshot read can see station 3, and the coordinator must grow from it.
    """
    entry = _entry(num_stations=2)
    client = _FilteringClient(2, {1: "A", 2: "B", 3: "New zone"})
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    # The dict read is provably blind to station 3 (models the real filter).
    assert 3 not in client._dict_read()
    # Yet the coordinator adopted the wider width through the snapshot.
    assert coordinator.device_station_count == 3
    assert coordinator.num_stations == 3
    assert len(coordinator.stations) == 3
    assert coordinator.station_names[3] == "New zone"


@pytest.mark.asyncio
async def test_device_station_count_never_shrinks_below_adopted_value(
    hass: HomeAssistant,
) -> None:
    """A snapshot whose count is lower keeps the existing width."""
    entry = _entry(num_stations=4)
    # Stations 3-4 exist physically but carry no onboard name: the snapshot
    # count (2) is below the configured width (4).
    client = _FilteringClient(4, {1: "A", 2: "B"})
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
    client = _FilteringClient(4, {1: "A"})
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        # First read: only station 1 named (fresh-install shape, names live
        # HA-side). The snapshot count (1) is below the configured width:
        # no shrink, trigger keeps firing.
        await coordinator._fetch_device_metadata()
        assert coordinator.device_station_count == 2
        assert coordinator.num_stations == 2

        # The user names station 3 onboard; ONE read grows the width
        # immediately through the snapshot path.
        client.on_device_names = {1: "A", 3: "New zone"}
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
    over read frequency).
    """
    entry = _entry(num_stations=4)
    client = _FilteringClient(4, {1: "A", 2: "B"})
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()
        assert coordinator.device_station_count == 4
        assert coordinator.num_stations == 4

        # The cached names (2) stay below num_stations (4): the trigger
        # keeps firing on every cycle.
        client.get_station_name_snapshot.reset_mock()
        await coordinator._fetch_device_metadata()
        client.get_station_name_snapshot.assert_awaited_once()
        await coordinator._fetch_device_metadata()
        assert client.get_station_name_snapshot.await_count == 2

    assert coordinator.num_stations == 4


@pytest.mark.asyncio
async def test_retry_trigger_stops_when_names_cover_width(
    hass: HomeAssistant,
) -> None:
    """A device whose names cover the width stops re-reading (no infinite loop)."""
    entry = _entry(num_stations=2)
    client = _FilteringClient(2, {1: "A", 2: "B"})
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()
        assert coordinator.num_stations == 2

        client.get_station_name_snapshot.reset_mock()
        await coordinator._fetch_device_metadata()

    client.get_station_name_snapshot.assert_not_awaited()
    assert coordinator.station_names == {1: "A", 2: "B"}


@pytest.mark.asyncio
async def test_num_stations_property_follows_device_count(
    hass: HomeAssistant,
) -> None:
    """The property returns the upward-only device-derived width."""
    entry = _entry(num_stations=4)
    client = _FilteringClient(4, {1: "A", 2: "B"})
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
    client = _FilteringClient(2, {1: "A", 2: "B", 3: "C", 4: "D"})
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
    client = _FilteringClient(2, {1: "A", 2: "B"})
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
    client = _FilteringClient(2, {1: "A", 2: "B", 3: "C"})
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
    client = _FilteringClient(2, {1: "A", 2: "B", 3: "C", 4: "D"})
    coordinator = _make_coordinator(hass, entry, client)

    with _mock_hass_storage():
        await coordinator._fetch_device_metadata()

    assert coordinator.device_station_count == 4
    assert coordinator.num_stations == 4
    # All four snapshot names pass the width filter, not just the
    # configured two.
    assert coordinator.station_names == {1: "A", 2: "B", 3: "C", 4: "D"}
    assert len(coordinator.stations) == 4


@pytest.mark.asyncio
async def test_pin_is_solem_blip_ble_0_3_2_b7() -> None:
    """The manifest and pyproject pin solem-blip-ble==0.3.2b7."""
    import pathlib

    import tomllib

    root = pathlib.Path(__file__).parent.parent
    manifest = json.loads(
        (root / "custom_components/solem_blip/manifest.json").read_text()
    )
    assert "solem-blip-ble==0.3.2b7" in manifest["requirements"]

    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    deps = pyproject["project"]["dependencies"]
    assert "solem-blip-ble==0.3.2b7" in deps
