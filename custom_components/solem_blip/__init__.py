"""The Solem BL-IP Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
]

type MyConfigEntry = ConfigEntry[RuntimeData]


@dataclass
class RuntimeData:
    """Runtime data attached to a config entry."""

    coordinator: SolemCoordinator
    cancel_update_listener: Callable


async def async_setup_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Set up Solem BL-IP from a config entry."""
    coordinator = SolemCoordinator(hass, config_entry)
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    await coordinator.async_init()

    if not coordinator.data:
        _LOGGER.warning(
            "%s - No initial sensor data; entities will retry on the %ss poll interval",
            coordinator.controller_mac_address,
            coordinator.poll_interval,
        )

    cancel_update_listener = config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_listener)
    )
    config_entry.runtime_data = RuntimeData(coordinator, cancel_update_listener)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    config_entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        f"{DOMAIN} initial BLE status refresh",
    )
    return True


async def _async_update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting the device from the UI."""
    return True


async def async_reconfigure_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Apply reconfigured entry data to the coordinator."""
    runtime_data: RuntimeData = hass.data[DOMAIN][config_entry.entry_id]
    await runtime_data.coordinator.update_config(config_entry)
    await runtime_data.coordinator.async_refresh()


async def async_unload_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if isinstance(coordinator, SolemCoordinator):
        await coordinator.async_shutdown()

    if not await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS):
        return False

    hass.data[DOMAIN].pop(config_entry.entry_id, None)
    return True
