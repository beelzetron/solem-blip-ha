"""Number platform for the Solem BL-IP integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entry import MyConfigEntry
from .base import SolemBaseEntity
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
    numbers: list[IrrigationManualDuration] = []

    for device in coordinator.data:
        device_type = device.get("device_type")
        if not device_type or device_type not in NUMBER_DESCRIPTIONS:
            continue
        description = NUMBER_DESCRIPTIONS[device_type]
        numbers.append(
            IrrigationManualDuration(
                coordinator, device, description.state_field, description
            )
        )

    async_add_entities(numbers)


class IrrigationManualDuration(SolemBaseEntity, NumberEntity):
    """Manual irrigation duration number entity."""

    entity_description: SolemNumberEntityDescription

    def __init__(
        self,
        coordinator: SolemCoordinator,
        device: dict[str, Any],
        parameter: str,
        description: SolemNumberEntityDescription,
    ) -> None:
        """Initialise number entity."""
        super().__init__(coordinator, device, parameter, description)

    @property
    def native_value(self) -> float | None:
        return float(self.coordinator.irrigation_manual_duration)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.irrigation_manual_duration = int(value)
        self.async_write_ha_state()
