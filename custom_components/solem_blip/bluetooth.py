"""Bluetooth discovery helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

BLUETOOTH_STARTUP_WAIT_TIMEOUT = 120
BLUETOOTH_STARTUP_WAIT_INTERVAL = 10


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


async def async_wait_for_connectable_device(
    hass: HomeAssistant,
    address: str,
    *,
    timeout: float = BLUETOOTH_STARTUP_WAIT_TIMEOUT,
    interval: float = BLUETOOTH_STARTUP_WAIT_INTERVAL,
) -> bool:
    """Wait until HA Bluetooth can route a connectable session to the device."""
    if async_get_connectable_device(hass, address) is not None:
        _LOGGER.info("%s is available for BLE connection", address)
        return True

    _LOGGER.info(
        "Waiting up to %ss for %s to become connectable in Home Assistant Bluetooth",
        int(timeout),
        address,
    )
    loops = max(1, int(timeout / interval))
    for attempt in range(loops):
        await asyncio.sleep(interval)
        if async_get_connectable_device(hass, address) is not None:
            _LOGGER.info(
                "%s is available for BLE connection after %ss",
                address,
                int((attempt + 1) * interval),
            )
            return True

    _LOGGER.warning(
        "%s is advertised but not connectable via Home Assistant Bluetooth after %ss",
        address,
        int(timeout),
    )
    return False
