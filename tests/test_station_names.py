"""Onboard naming safety: complete reads, byte limits, conflicts, recovery.

Mirrors the fork's tests (ThomasHFWright/solem-blip-ha PR #1,
tests/test_station_names.py) adapted to this integration's architecture
(``StationNameManager`` + coordinator + options flow, with the BLE
frame logic in the separate solem_blip_ble library).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.solem_blip.config_flow import SolemOptionsFlowHandler
from custom_components.solem_blip.station_names import StationNameManager
from solem_blip_ble import protocol
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
async def manager(hass, mock_solem_client):
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
    mgr = StationNameManager(hass, "names-test", mock_solem_client)
    await mgr.async_load()
    await mgr.refresh()
    return mgr


def test_utf8_exact_limit_split_and_station_six():
    """32-byte names (ASCII, accented, emoji) split into two exact frames."""
    for name in ("a" * 32, "a" * 15 + "é" + "b" * 15, "🌱" * 8):
        frames = protocol.pack_station_name(6, name, 6)
        assert [f[:4] for f in frames] == [
            b"\x33\x12\x00\x05",
            b"\x33\x12\x01\x05",
        ]
        assert all(len(f) == 20 for f in frames)
        assert frames[0][4:] + frames[1][4:] == name.encode()


@pytest.mark.parametrize(
    ("station", "name"),
    [
        (0, "ok"),
        (7, "ok"),
        (True, "ok"),
        (1.5, "ok"),
        (1, ""),
        (1, "  "),
        (1, "a\0b"),
        (1, "a" * 33),
        (1, "🌱" * 9),
        (1, None),
    ],
)
def test_invalid_names_never_encode(station, name):
    with pytest.raises(ValueError):
        protocol.pack_station_name(station, name, 6)


async def test_update_only_selected_name_changes(manager, mock_solem_client, hass):
    """Same-name update writes nothing; rename changes only the target."""
    before = manager.snapshot

    async def fake_write(station, name, expected, *, before):
        return expected

    mock_solem_client.write_station_name = AsyncMock(side_effect=fake_write)
    assert await manager.update(1, before.names[1], before.revision) == before
    mock_solem_client.write_station_name.assert_not_awaited()

    after = await manager.update(6, "06 - Kitchen garden", before.revision)
    assert after.names[6] == "06 - Kitchen garden"
    assert mock_solem_client.write_station_name.await_count == 1
    written = mock_solem_client.write_station_name.await_args
    assert written is not None
    assert written.args[0] == 6 and written.args[1] == "06 - Kitchen garden"
    assert manager.pending is None and manager.last_write
    assert {
        k: v for k, v in after.raw_names.items() if k != 6
    } == {k: v for k, v in before.raw_names.items() if k != 6}

    restored = StationNameManager(hass, "names-test", mock_solem_client)
    await restored.async_load()
    assert restored.last_write == manager.last_write
    assert restored.snapshot is None


async def test_stale_foreign_station_change_and_invalid_input_do_not_write(
    manager, mock_solem_client
):
    before = manager.snapshot
    changed = before.renamed(2, "Changed in phone", 6)
    mock_solem_client.get_station_name_snapshot = AsyncMock(return_value=changed)
    mock_solem_client.write_station_name = AsyncMock()
    with pytest.raises(StaleProgram):
        await manager.update(6, "New name", before.revision)
    mock_solem_client.write_station_name.assert_not_awaited()
    mock_solem_client.get_status.reset_mock()
    with pytest.raises(ValueError):
        await manager.update(6, "🌱" * 9, before.revision)
    mock_solem_client.get_status.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "firmware"),
    [
        ({"is_watering": True, "controller_state": "On"}, 5),
        ({"controller_state": "Off"}, 5),
        ({"is_watering": False, "controller_state": "Unknown"}, 5),
        ({"is_watering": False, "controller_state": "Off"}, 6),
    ],
)
async def test_busy_unknown_and_unsupported_fail_closed(
    manager, mock_solem_client, status, firmware
):
    mock_solem_client.get_status.return_value = status
    if "firmware" not in status:
        mock_solem_client.get_firmware_version.return_value = {
            "major": firmware,
            "raw_hex": f"{firmware}.0.0",
        }
    mock_solem_client.write_station_name = AsyncMock()
    with pytest.raises(InvalidSnapshot):
        await manager.update(1, "New", manager.snapshot.revision)
    mock_solem_client.write_station_name.assert_not_awaited()


@pytest.mark.parametrize("error", [UncertainWrite("lost"), asyncio.CancelledError()])
async def test_interruption_persists_journal_and_does_not_replay(
    manager, mock_solem_client, hass, error
):
    before = manager.snapshot
    mock_solem_client.write_station_name = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await manager.update(6, "New", before.revision)
    assert manager.pending
    restored = StationNameManager(hass, "names-test", mock_solem_client)
    await restored.async_load()
    assert restored.pending == manager.pending
    mock_solem_client.write_station_name = AsyncMock()
    with pytest.raises(UncertainWrite):
        await restored.update(6, "New", before.revision)
    mock_solem_client.write_station_name.assert_not_awaited()
    # A fresh read where the device still has the old names reconciles.
    await restored.refresh()
    assert restored.pending is None


async def test_partial_write_requires_explicit_review(manager, mock_solem_client):
    before = manager.snapshot
    mock_solem_client.write_station_name = AsyncMock(
        side_effect=UncertainWrite("lost")
    )
    with pytest.raises(UncertainWrite):
        await manager.update(6, "New long name", before.revision)
    partial = before.renamed(6, "Partial name", 6)
    mock_solem_client.get_station_name_snapshot = AsyncMock(return_value=partial)
    await manager.refresh()
    assert manager.pending  # foreign revision: journal stays, review required
    await manager.refresh(accept_current=True)
    assert manager.pending is None and manager.snapshot.names[6] == "Partial name"
    assert mock_solem_client.write_station_name.await_count == 1


async def test_matching_revision_journal_clears_on_refresh(
    manager, mock_solem_client
):
    before = manager.snapshot
    mock_solem_client.write_station_name = AsyncMock(
        side_effect=UncertainWrite("lost")
    )
    with pytest.raises(UncertainWrite):
        await manager.update(6, "New", before.revision)
    await manager.refresh()
    assert manager.pending is None


async def test_storage_failure_never_allows_unjournalled_write(
    manager, mock_solem_client
):
    manager.store.async_save = AsyncMock(side_effect=OSError("disk full"))
    mock_solem_client.write_station_name = AsyncMock()
    with pytest.raises(OSError):
        await manager.update(1, "New", manager.snapshot.revision)
    mock_solem_client.write_station_name.assert_not_awaited()
    assert manager.pending


async def test_pending_blocks_further_writes(manager, mock_solem_client, hass):
    mock_solem_client.write_station_name = AsyncMock(
        side_effect=UncertainWrite("lost")
    )
    with pytest.raises(UncertainWrite):
        await manager.update(6, "New", manager.snapshot.revision)
    assert manager.pending
    with pytest.raises(UncertainWrite):
        await manager.update(2, "Other", manager.snapshot.revision)
    assert mock_solem_client.write_station_name.await_count == 1
