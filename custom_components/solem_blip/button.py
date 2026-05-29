"""Button platform for the Solem BL-IP integration."""

from dataclasses import dataclass
import asyncio
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator


@dataclass
class ButtonTypeClass:
    """Map coordinator device types to button classes."""

    device_type: str
    button_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up Solem BL-IP buttons."""
    coordinator: SolemCoordinator = config_entry.runtime_data.coordinator

    button_types = [
        ButtonTypeClass("SPRINKLE_BUTTON", IrrigationStartButton),
        ButtonTypeClass("STOP_BUTTON", IrrigationStopButton),
        ButtonTypeClass("ON_BUTTON", ControllerOnButton),
        ButtonTypeClass("OFF_BUTTON", ControllerOffButton),
    ]

    buttons = []
    for button_type in button_types:
        buttons.extend(
            [
                button_type.button_class(coordinator, device)
                for device in coordinator.data
                if device.get("device_type") == button_type.device_type
            ]
        )

    async_add_entities(buttons)


class SolemButtonEntity(SolemBaseEntity, ButtonEntity):
    def __init__(self, coordinator: SolemCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, None)

    @property
    def entity_category(self):
        return EntityCategory.CONFIG


class IrrigationStartButton(SolemButtonEntity):
    async def async_press(self) -> None:
        station = int(self.device_id.rsplit("_", 1)[-1])
        asyncio.create_task(self.coordinator.start_irrigation(station))


class IrrigationStopButton(SolemButtonEntity):
    async def async_press(self) -> None:
        asyncio.create_task(self.coordinator.stop_irrigation())


class ControllerOnButton(SolemButtonEntity):
    async def async_press(self) -> None:
        asyncio.create_task(self.coordinator.turn_controller_on())


class ControllerOffButton(SolemButtonEntity):
    async def async_press(self) -> None:
        asyncio.create_task(self.coordinator.turn_controller_off())
