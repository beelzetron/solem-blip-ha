"""The Solem BL-IP Home Assistant integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .config_entry import MyConfigEntry, RuntimeData
from .const import CONTROLLER_MAC_ADDRESS, DOMAIN
from .coordinator import SolemCoordinator

__all__ = ["DOMAIN", "MyConfigEntry", "RuntimeData", "PLATFORMS"]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Set up Solem BL-IP from a config entry."""
    coordinator = SolemCoordinator(hass, config_entry)

    await coordinator.async_init()

    listener = config_entry.add_update_listener(_async_update_listener)
    config_entry.async_on_unload(listener)
    config_entry.runtime_data = RuntimeData(coordinator, listener)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        config_entry.runtime_data = None  # type: ignore[assignment]
        raise

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant, config_entry: MyConfigEntry
) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS):
        return False

    await config_entry.runtime_data.coordinator.async_shutdown()
    config_entry.runtime_data = None  # type: ignore[assignment]
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> None:
    """Request fresh Bluetooth discovery after an entry is removed."""
    from homeassistant.components import bluetooth

    rediscover = getattr(bluetooth, "async_rediscover_address", None)
    if rediscover is not None:
        address = config_entry.data[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[1]
        rediscover(hass, address)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: MyConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing the controller device for this config entry."""
    return True
