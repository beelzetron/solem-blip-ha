"""Binary sensor platform for the Solem BL-IP integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entry import MyConfigEntry
from .entity import SolemBaseEntity
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
    binary_sensors: list[SolemBinarySensorEntity] = []

    for device in coordinator.data:
        device_type = device.get("device_type")
        if not device_type or device_type not in BINARY_SENSOR_DESCRIPTIONS:
            continue
        description = BINARY_SENSOR_DESCRIPTIONS[device_type]
        entity_class = BINARY_SENSOR_ENTITY_CLASSES.get(
            device_type, SolemBinarySensorEntity
        )
        binary_sensors.append(
            entity_class(coordinator, device, description.state_field, description)
        )

    async_add_entities(binary_sensors)


class SolemBinarySensorEntity(SolemBaseEntity, BinarySensorEntity):
    """Generic Solem BL-IP binary sensor."""

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
        return bool(self._descriptor_field())


class BatteryLow(SolemBinarySensorEntity):
    """Battery low alert binary sensor."""

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.battery_low


class ProgramRunning(SolemBinarySensorEntity):
    """Program run in progress (from status byte 8)."""

    @property
    def is_on(self) -> bool | None:
        program_num = self.device.get("program_num")
        if not isinstance(program_num, int):
            return False
        return self.coordinator.active_program_num == program_num


BINARY_SENSOR_ENTITY_CLASSES: dict[str, type[SolemBinarySensorEntity]] = {
    "BATTERY_LOW_SENSOR": BatteryLow,
    "PROGRAM_RUNNING_SENSOR": ProgramRunning,
}
