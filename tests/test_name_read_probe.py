"""Tests for the run_name_read_probe diagnostic service (issue #136)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from solem_blip_ble.exceptions import SolemConnectionError

from custom_components.solem_blip import RuntimeData
from custom_components.solem_blip.const import DOMAIN
from custom_components.solem_blip.coordinator import SolemCoordinator
from custom_components.solem_blip.services import (
    SERVICE_RUN_NAME_READ_PROBE,
    async_setup_services,
)
from tests.conftest import create_mock_solem_client


def _snapshot_mock(client: MagicMock, count: int = 6) -> None:
    snapshot = MagicMock()
    snapshot.reported_count = count
    snapshot.station_count = count
    snapshot.names = {i: f"Zone {i}" for i in range(1, count + 1)}
    client.get_station_name_snapshot = AsyncMock(return_value=snapshot)


async def _setup_probe_target(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> tuple[SolemCoordinator, str]:
    """Set up a coordinator + registered services, with BLE fully mocked."""
    client = create_mock_solem_client(2)
    _snapshot_mock(client)
    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()

    mock_config_entry.add_to_hass(hass)
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, coordinator.controller_mac_address)},
    )
    await async_setup_services(hass)
    return coordinator, device.id


@pytest.mark.asyncio
async def test_probe_reports_matrix_both_shapes_ok(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Both shapes succeed: verdict reports a clean run; clients are per-shape."""
    coordinator, device_id = await _setup_probe_target(hass, mock_config_entry)

    clients = []
    snapshot = MagicMock()
    snapshot.reported_count = 2
    snapshot.station_count = 2
    snapshot.names = {1: "A", 2: "B"}

    def make_client(*args, **kwargs):
        client = create_mock_solem_client(2)
        _snapshot_mock(client)
        clients.append(client)
        return client

    async def instant_sleep(_seconds: float) -> None:
        pass

    with patch(
        "custom_components.solem_blip.name_read_probe.build_solem_client",
        side_effect=make_client,
    ), patch(
        "custom_components.solem_blip.name_read_probe.asyncio.sleep",
        side_effect=instant_sleep,
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_NAME_READ_PROBE,
            {"device_id": device_id, "delay": 0},
            blocking=True,
            return_response=True,
        )

    result = response["result"]
    assert result["shape_a"]["ok"] is True
    assert result["shape_b"]["ok"] is True
    assert "name_detail" in result["shape_b"]
    assert result["verdict"] == "both shapes worked in this run"
    # One fresh client per shape: A's snapshot ran on its own connection.
    assert len(clients) == 2
    clients[0].get_station_name_snapshot.assert_awaited_once()
    clients[1].get_status.assert_awaited_once()
    clients[1].get_station_name_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_reports_b_name_failure_verdict(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A works, B's name read fails: the verdict names the commit trigger."""
    coordinator, device_id = await _setup_probe_target(hass, mock_config_entry)

    from solem_blip_ble.exceptions import SolemConnectionError

    clients: list[MagicMock] = []

    def make_client(*args, **kwargs):
        client = create_mock_solem_client(2)
        _snapshot_mock(client)
        # Shape A runs first (succeeds), shape B's name read fails on every
        # attempt (clients 1 and 2).
        if len(clients) >= 1:
            client.get_station_name_snapshot = AsyncMock(
                side_effect=SolemConnectionError("silent link")
            )
        clients.append(client)
        return client

    async def instant_sleep(_seconds: float) -> None:
        pass

    with patch(
        "custom_components.solem_blip.name_read_probe.build_solem_client",
        side_effect=make_client,
    ), patch(
        "custom_components.solem_blip.name_read_probe.asyncio.sleep",
        side_effect=instant_sleep,
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_NAME_READ_PROBE,
            {"device_id": device_id, "delay": 0},
            blocking=True,
            return_response=True,
        )

    result = response["result"]
    assert result["shape_a"]["ok"] is True
    assert result["shape_b"]["ok"] is True  # status succeeded...
    assert "name_error" in result["shape_b"]  # ...names did not
    assert "preceding commit" in result["verdict"]
