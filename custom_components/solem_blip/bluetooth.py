"""Bluetooth discovery helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def async_scan_devices(hass: HomeAssistant, connectable: bool = True) -> list[Any]:
    """Return BLE devices from Home Assistant discovery."""
    try:
        from homeassistant.components.bluetooth import async_discovered_service_info

        return [
            info.device
            for info in async_discovered_service_info(hass, connectable)
        ]
    except Exception:
        from bleak import BleakScanner

        return await BleakScanner.discover(timeout=5.0)


def async_get_connectable_device(hass: HomeAssistant, address: str) -> Any | None:
    """Return the HA-resolved BLEDevice for a connectable controller."""
    from homeassistant.components import bluetooth

    return bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )


def async_is_device_discovered(hass: HomeAssistant, address: str) -> bool:
    """Return True when HA has recently seen this controller advertising."""
    from homeassistant.components.bluetooth import async_discovered_service_info

    normalized = address.lower()
    return any(
        info.address.lower() == normalized
        for info in async_discovered_service_info(hass, connectable=True)
    )
