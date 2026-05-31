"""Number platform for the Solem BL-IP integration."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator

PARALLEL_UPDATES = 1


@dataclass(frozen=True)
class NumberTypeClass:
    """Map coordinator device types to number classes."""

    device_type: str
    state_field: str
    number_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up Solem BL-IP numbers."""
    coordinator: SolemCoordinator = config_entry.runtime_data.coordinator

    numbers = []
    for number_type in NUMBER_TYPES:
        numbers.extend(
            [
                number_type.number_class(coordinator, device, number_type.state_field)
                for device in coordinator.data
                if device.get("device_type") == number_type.device_type
            ]
        )

    async_add_entities(numbers)


class SolemNumberEntity(SolemBaseEntity, NumberEntity):
    @property
    def entity_category(self):
        return EntityCategory.CONFIG


class IrrigationManualDuration(SolemNumberEntity):
    def __init__(
        self, coordinator: SolemCoordinator, device: dict[str, Any], parameter: str
    ):
        super().__init__(coordinator, device, parameter)
        self._attr_native_min_value = 1
        self._attr_native_max_value = 60
        self._attr_native_step = 1

    @property
    def native_value(self) -> float | None:
        return self.coordinator.irrigation_manual_duration

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.irrigation_manual_duration = int(value)
        self.async_write_ha_state()


NUMBER_TYPES = (
    NumberTypeClass("IRRIGATION_DURATION_NUMBER", "value", IrrigationManualDuration),
)
