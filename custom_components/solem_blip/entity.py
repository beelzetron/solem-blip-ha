"""Base entity which all other entity platform classes can inherit."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolemCoordinator
from .util import format_entity_unique_id

_LOGGER = logging.getLogger(__name__)


class SolemBaseEntity(CoordinatorEntity[SolemCoordinator]):
    """Base entity wired to the Solem BL-IP DataUpdateCoordinator."""

    coordinator: SolemCoordinator
    entity_description: EntityDescription

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolemCoordinator,
        device: dict[str, Any],
        parameter: str | None,
        description: EntityDescription,
    ) -> None:
        """Initialise entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self.device = device
        self.device_id = device["device_id"]
        self.parameter = parameter
        if device.get("device_type") == "STATE_SENSOR":
            self._attr_translation_key = (
                "station_status"
                if "_station_" in self.device_id
                else "controller_status"
            )
        self._apply_descriptor_metadata(device)

    def _apply_descriptor_metadata(self, device: dict[str, Any] | None) -> None:
        """Apply dynamic descriptor metadata from coordinator descriptors."""
        if device is None:
            return
        if placeholders := device.get("translation_placeholders"):
            self._attr_translation_placeholders = placeholders
            self._attr_name = str(device.get("device_name") or "")
        elif hasattr(self, "_attr_translation_placeholders"):
            del self._attr_translation_placeholders
            self._attr_name = None
        else:
            self._attr_name = None

    def _descriptor_field(self, field: str | None = None) -> Any:
        """Return one field from the entity descriptor."""
        key = self.parameter if field is None else field
        if key is None:
            return None
        return self.coordinator.get_device_parameter(self.device_id, key)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update entity with latest coordinator data."""
        self.device = self.coordinator.get_device(self.device_id) or self.device
        if self.device is not None:
            self._apply_descriptor_metadata(self.device)
        _LOGGER.debug(
            "Updating device: %s, %s",
            self.device_id,
            self.coordinator.get_device_parameter(self.device_id, "device_name"),
        )
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            name=self.coordinator.controller_mac_address,
            manufacturer="Solem",
            model="BL-IP",
            sw_version=self.coordinator.firmware_version,
            identifiers={
                (
                    DOMAIN,
                    self.coordinator.controller_mac_address,
                )
            },
            connections={
                (CONNECTION_BLUETOOTH, self.coordinator.controller_mac_address)
            },
        )

    @property
    def unique_id(self) -> str:
        """Return stable unique id derived from MAC and device_id."""
        return format_entity_unique_id(
            self.coordinator.controller_mac_address,
            self.device_id,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the extra state attributes."""
        return {}
