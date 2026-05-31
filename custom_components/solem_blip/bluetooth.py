"""Bluetooth discovery helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import V5_SERVICE_UUID


async def async_scan_devices(hass: HomeAssistant, connectable: bool = True) -> list[Any]:
    """Return compatible BLE devices from Home Assistant discovery."""
    from homeassistant.components.bluetooth import async_discovered_service_info

    return [
        info.device
        for info in async_discovered_service_info(hass, connectable)
        if V5_SERVICE_UUID in {uuid.lower() for uuid in (info.service_uuids or [])}
    ]


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
