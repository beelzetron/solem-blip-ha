"""Button platform for the Solem BL-IP integration."""

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyConfigEntry
from .api import APIConnectionError
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)


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

    async def _press(self, action: str, coro) -> None:
        """Run a coordinator action and surface BLE failures to the UI."""
        try:
            await coro
        except APIConnectionError as err:
            _LOGGER.error("%s failed: %s", action, err)
            raise HomeAssistantError(f"{action} failed: {err}") from err


class IrrigationStartButton(SolemButtonEntity):
    async def async_press(self) -> None:
        station = int(self.device_id.rsplit("_", 1)[-1])
        await self._press(
            f"Start irrigation on station {station}",
            self.coordinator.start_irrigation(station),
        )


class IrrigationStopButton(SolemButtonEntity):
    async def async_press(self) -> None:
        await self._press("Stop irrigation", self.coordinator.stop_irrigation())


class ControllerOnButton(SolemButtonEntity):
    async def async_press(self) -> None:
        await self._press("Turn controller on", self.coordinator.turn_controller_on())


class ControllerOffButton(SolemButtonEntity):
    async def async_press(self) -> None:
        await self._press(
            "Turn controller off", self.coordinator.turn_controller_off()
        )
