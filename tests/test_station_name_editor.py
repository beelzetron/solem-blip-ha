"""Options-flow station-name editor tests.

Mirrors the fork's editor cases (ThomasHFWright/solem-blip-ha PR #1)
adapted to this integration's options flow structure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from custom_components.solem_blip.config_flow import SolemOptionsFlowHandler
from custom_components.solem_blip.station_names import StationNameManager
from solem_blip_ble.exceptions import (
    InvalidSnapshot,
    StaleProgram,
    UncertainWrite,
)
from solem_blip_ble.station_names import StationNameSnapshot


def _snapshot(num: int = 6) -> StationNameSnapshot:
    return StationNameSnapshot(
        {i: f"Station {i}".encode().ljust(32, b"\0") for i in range(1, 13)}
    )


@pytest.fixture
async def names_manager(hass, mock_solem_client):
    mock_solem_client.max_station_num = 6
    mock_solem_client.get_status = AsyncMock(
        return_value={"is_watering": False, "controller_state": "Off"}
    )
    mock_solem_client.get_firmware_version = AsyncMock(
        return_value={"major": 5, "raw_hex": "5.1.5"}
    )
    mock_solem_client.get_station_name_snapshot = AsyncMock(
        return_value=_snapshot()
    )
    mgr = StationNameManager(hass, "names-editor", mock_solem_client)
    await mgr.async_load()
    await mgr.refresh()
    return mgr


@pytest.fixture
async def editor(hass, mock_config_entry, coordinator, names_manager):
    from custom_components.solem_blip.config_entry import RuntimeData

    mock_config_entry.add_to_hass(hass)
    coordinator.station_name_manager = names_manager
    coordinator.api = names_manager.api
    coordinator.num_stations = 6
    coordinator.async_update_all_sensors = AsyncMock(return_value=[])
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        yield handler, coordinator, mock_config_entry


async def test_editor_reads_and_saves_only_after_submit(editor, mock_solem_client):
    flow, coordinator, entry = editor
    manager = coordinator.station_name_manager

    async def fake_write(station, name, expected, *, before):
        return expected

    mock_solem_client.write_station_name = AsyncMock(side_effect=fake_write)
    result = await flow.async_step_station_select()
    assert result["step_id"] == "station_select"
    result = await flow.async_step_station_select({"station": "6"})
    assert result["step_id"] == "station_name"
    assert result["data_schema"]({}) == {"name": "Station 6"}
    mock_solem_client.write_station_name.assert_not_awaited()
    result = await flow.async_step_station_name({"name": "06 - Garden"})
    assert result["type"] == "create_entry"
    assert manager.snapshot.names[6] == "06 - Garden"
    assert coordinator.station_names[6] == "06 - Garden"
    assert len(coordinator.station_names) == 6


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (StaleProgram("changed"), "stale_station_name"),
        (ValueError("bad"), "invalid_station_name"),
        (OSError("offline"), "station_name_failed"),
        (UncertainWrite("lost"), "station_name_uncertain"),
        (InvalidSnapshot("busy"), "station_name_busy"),
    ],
)
async def test_editor_write_errors_keep_draft(editor, error, key):
    flow, coordinator, _ = editor
    await flow.async_step_station_select({"station": "1"})
    coordinator.rename_station = AsyncMock(side_effect=error)
    result = await flow.async_step_station_name({"name": "Typed name"})
    assert key in result["errors"].values()
    assert result["data_schema"]({})["name"] == "Typed name"


async def test_editor_recovery_requires_explicit_accept(editor):
    flow, coordinator, _ = editor
    before = coordinator.station_name_manager.snapshot
    manager = coordinator.station_name_manager
    manager.pending = {
        "before_revision": "old",
        "expected_revision": "expected",
    }
    result = await flow.async_step_station_select({"station": "6"})
    assert result["errors"]["base"] == "station_name_uncertain"
    result = await flow.async_step_station_select(
        {"station": "6", "accept_current": True}
    )
    assert result["step_id"] == "station_name"
    assert manager.pending is None
    assert manager.snapshot == before


async def test_editor_read_failure_and_unloaded_abort(editor, mock_config_entry):
    flow, coordinator, entry = editor
    manager = coordinator.station_name_manager
    manager.api.get_station_name_snapshot = AsyncMock(side_effect=OSError("offline"))
    result = await flow.async_step_station_select()
    assert result["reason"] == "station_names_read_failed"
    # Without a snapshot the name step cannot render at all.
    coordinator.station_name_manager.snapshot = None
    result = await flow.async_step_station_name()
    assert result["reason"] == "station_names_read_failed"
    entry.runtime_data = None
    result = await flow.async_step_station_select()
    assert result["type"] == "abort"


async def test_editor_invalid_station_aborts(editor):
    flow, coordinator, _ = editor
    await flow.async_step_station_select()
    result = await flow.async_step_station_select({"station": "7"})
    assert result["type"] == "abort"


async def test_editor_recovery_read_failure_aborts(editor):
    flow, coordinator, _ = editor
    manager = coordinator.station_name_manager
    manager.pending = {"before_revision": "old", "expected_revision": "new"}
    manager.api.get_station_name_snapshot = AsyncMock(side_effect=OSError("offline"))
    result = await flow.async_step_station_select(
        {"station": "1", "accept_current": True}
    )
    assert result["type"] == "abort"
