"""The Solem BL-IP Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONTROLLER_MAC_ADDRESS, DOMAIN
from .coordinator import SolemCoordinator

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

    await coordinator.async_init()

    cancel_update_listener = config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_listener)
    )
    config_entry.runtime_data = RuntimeData(coordinator, cancel_update_listener)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        config_entry.runtime_data = None
        raise

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS):
        return False

    await config_entry.runtime_data.coordinator.async_shutdown()
    config_entry.runtime_data = None
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> None:
    """Request fresh Bluetooth discovery after an entry is removed."""
    from homeassistant.components import bluetooth

    rediscover = getattr(bluetooth, "async_rediscover_address", None)
    if rediscover is not None:
        address = config_entry.data[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[1]
        rediscover(hass, address)
