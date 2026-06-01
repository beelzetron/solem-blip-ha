"""Bluetooth helper tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.solem_blip.const import V5_SERVICE_UUID


@pytest.mark.asyncio
async def test_scan_devices_filters_by_service_uuid(hass: HomeAssistant) -> None:
    """Scan returns only devices advertising the BL-IP service UUID."""
    matching = SimpleNamespace(
        device=SimpleNamespace(name="Solem", address="AA:BB:CC:DD:EE:FF"),
        service_uuids=[V5_SERVICE_UUID],
    )
    other = SimpleNamespace(
        device=SimpleNamespace(name="Other", address="11:22:33:44:55:66"),
        service_uuids=["0000"],
    )

    mock_bluetooth = MagicMock()
    mock_bluetooth.async_discovered_service_info = MagicMock(return_value=[matching, other])

    with patch.dict(
        "sys.modules",
        {"homeassistant.components.bluetooth": mock_bluetooth},
    ):
        from custom_components.solem_blip import bluetooth as bl_module

        devices = await bl_module.async_scan_devices(hass)

    assert devices == [matching.device]


def test_get_connectable_device_returns_ble_device(hass: HomeAssistant) -> None:
    """Connectable device lookup delegates to the HA Bluetooth stack."""
    ble_device = MagicMock()
    mock_bluetooth = MagicMock()
    mock_bluetooth.async_ble_device_from_address = MagicMock(return_value=ble_device)

    with patch.dict(
        "sys.modules",
        {"homeassistant.components.bluetooth": mock_bluetooth},
    ):
        from custom_components.solem_blip import bluetooth as bl_module

        result = bl_module.async_get_connectable_device(hass, "AA:BB:CC:DD:EE:FF")

    assert result is ble_device


def test_is_device_discovered_matches_address(hass: HomeAssistant) -> None:
    """Discovery helper matches controller addresses case-insensitively."""
    info = SimpleNamespace(address="aa:bb:cc:dd:ee:ff")
    mock_bluetooth = MagicMock()
    mock_bluetooth.async_discovered_service_info = MagicMock(return_value=[info])

    with patch.dict(
        "sys.modules",
        {"homeassistant.components.bluetooth": mock_bluetooth},
    ):
        from custom_components.solem_blip import bluetooth as bl_module

        assert bl_module.async_is_device_discovered(hass, "AA:BB:CC:DD:EE:FF") is True
