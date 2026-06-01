"""Binary sensor platform for the Solem BL-IP integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entry import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator
from .entity_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
    SolemBinarySensorEntityDescription,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem BL-IP binary sensors."""
    coordinator = config_entry.runtime_data.coordinator
    binary_sensors: list[BatteryLow] = []

    for device in coordinator.data:
        device_type = device.get("device_type")
        if not device_type or device_type not in BINARY_SENSOR_DESCRIPTIONS:
            continue
        description = BINARY_SENSOR_DESCRIPTIONS[device_type]
        binary_sensors.append(
            BatteryLow(coordinator, device, description.state_field, description)
        )

    async_add_entities(binary_sensors)


class BatteryLow(SolemBaseEntity, BinarySensorEntity):
    """Battery low alert binary sensor."""

    entity_description: SolemBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: SolemCoordinator,
        device: dict[str, Any],
        parameter: str,
        description: SolemBinarySensorEntityDescription,
    ) -> None:
        """Initialise binary sensor."""
        super().__init__(coordinator, device, parameter, description)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.battery_low
