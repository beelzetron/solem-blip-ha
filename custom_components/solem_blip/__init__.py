"""The Solem BL-IP Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .config_entry import MyConfigEntry, RuntimeData
from .const import CONTROLLER_MAC_ADDRESS, DOMAIN
from .coordinator import SolemCoordinator
from .migrate import async_migrate_unique_ids, async_remove_program_name_entities
from .bluetooth_issue import CONSECUTIVE_FAILURES_THRESHOLD

CONFIG_VERSION = 2

__all__ = ["DOMAIN", "MyConfigEntry", "RuntimeData", "PLATFORMS", "CONFIG_VERSION"]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
]


async def async_migrate_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Migrate config entries to the latest version."""
    if config_entry.version >= CONFIG_VERSION:
        return True

    if config_entry.version == 1:
        await async_migrate_unique_ids(hass, config_entry)

    hass.config_entries.async_update_entry(config_entry, version=CONFIG_VERSION)
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Set up Solem BL-IP from a config entry."""
    await async_remove_program_name_entities(hass, config_entry)

    coordinator = SolemCoordinator(hass, config_entry)

    await coordinator.async_init()

    listener = config_entry.add_update_listener(_async_update_listener)
    config_entry.async_on_unload(listener)
    config_entry.runtime_data = RuntimeData(coordinator, listener)
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    coordinator.schedule_coordinator.async_start_first_refresh()
    config_entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        f"{DOMAIN} initial BLE status refresh",
    )
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
    """Allow removing the controller only after repeated failed updates."""
    return (
        config_entry.runtime_data.coordinator._consecutive_update_failures
        >= CONSECUTIVE_FAILURES_THRESHOLD
    )
