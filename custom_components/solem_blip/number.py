"""Number platform for the Solem BL-IP integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entry import MyConfigEntry
from .entity import SolemBaseEntity
from .coordinator import SolemCoordinator
from .entity_descriptions import NUMBER_DESCRIPTIONS, SolemNumberEntityDescription

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem BL-IP numbers."""
    coordinator = config_entry.runtime_data.coordinator
    numbers: list[SolemNumberEntity] = []

    for device in coordinator.data:
        device_type = device.get("device_type")
        if not device_type or device_type not in NUMBER_DESCRIPTIONS:
            continue
        description = NUMBER_DESCRIPTIONS[device_type]
        entity_class = NUMBER_ENTITY_CLASSES[device_type]
        numbers.append(entity_class(coordinator, device, description))

    async_add_entities(numbers)


class SolemNumberEntity(SolemBaseEntity, NumberEntity):
    """Base number entity for Solem BL-IP."""

    entity_description: SolemNumberEntityDescription

    def __init__(
        self,
        coordinator: SolemCoordinator,
        device: dict[str, Any],
        description: SolemNumberEntityDescription,
    ) -> None:
        """Initialise number entity."""
        super().__init__(coordinator, device, description.state_field, description)


class IrrigationManualDuration(SolemNumberEntity):
    """Manual irrigation duration number entity."""

    @property
    def native_value(self) -> float | None:
        return float(self.coordinator.irrigation_manual_duration)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.irrigation_manual_duration = int(value)
        self.async_write_ha_state()


class ControllerOffDays(SolemNumberEntity):
    """Number of days for temporary controller off command."""

    @property
    def native_value(self) -> float | None:
        return float(self.coordinator.controller_off_days)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.controller_off_days = int(value)
        self.async_write_ha_state()


NUMBER_ENTITY_CLASSES: dict[str, type[SolemNumberEntity]] = {
    "IRRIGATION_DURATION_NUMBER": IrrigationManualDuration,
    "CONTROLLER_OFF_DAYS_NUMBER": ControllerOffDays,
}
