"""Button platform for the Solem BL-IP integration."""

from __future__ import annotations

import logging
from collections.abc import Coroutine
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entry import MyConfigEntry
from .api import APIConnectionError
from .entity import SolemBaseEntity
from .const import DOMAIN
from .coordinator import SolemCoordinator
from .entity_descriptions import BUTTON_DESCRIPTIONS, SolemButtonEntityDescription

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem BL-IP buttons."""
    coordinator = config_entry.runtime_data.coordinator
    buttons: list[SolemButtonEntity] = []

    for device in coordinator.data:
        device_type = device.get("device_type")
        if not device_type or device_type not in BUTTON_DESCRIPTIONS:
            continue
        description = BUTTON_DESCRIPTIONS[device_type]
        entity_class = BUTTON_ENTITY_CLASSES[device_type]
        buttons.append(entity_class(coordinator, device, description))

    async_add_entities(buttons)


class SolemButtonEntity(SolemBaseEntity, ButtonEntity):
    """Base button entity for Solem BL-IP."""

    entity_description: SolemButtonEntityDescription

    def __init__(
        self,
        coordinator: SolemCoordinator,
        device: dict[str, Any],
        description: SolemButtonEntityDescription,
    ) -> None:
        """Initialise button."""
        super().__init__(coordinator, device, None, description)

    async def _press(
        self,
        translation_key: str,
        coro: Coroutine[Any, Any, None],
        *,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        """Run a coordinator action and surface BLE failures to the UI."""
        try:
            await coro
        except APIConnectionError as err:
            _LOGGER.error("Button action failed: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders=translation_placeholders,
            ) from err


class IrrigationStartButton(SolemButtonEntity):
    """Start manual irrigation on one station."""

    async def async_press(self) -> None:
        station = int(self.device_id.rsplit("_", 1)[-1])
        await self._press(
            "start_irrigation_failed",
            self.coordinator.start_irrigation(station),
            translation_placeholders={"station": str(station)},
        )


class IrrigationStopButton(SolemButtonEntity):
    """Stop active manual irrigation."""

    async def async_press(self) -> None:
        await self._press(
            "stop_irrigation_failed",
            self.coordinator.stop_irrigation(),
        )


class ControllerOnButton(SolemButtonEntity):
    """Turn the irrigation controller on."""

    async def async_press(self) -> None:
        await self._press(
            "controller_on_failed",
            self.coordinator.turn_controller_on(),
        )


class ControllerOffButton(SolemButtonEntity):
    """Turn the irrigation controller off."""

    async def async_press(self) -> None:
        await self._press(
            "controller_off_failed",
            self.coordinator.turn_controller_off(),
        )


class ControllerOffDaysButton(SolemButtonEntity):
    """Turn the irrigation controller off for the configured number of days."""

    async def async_press(self) -> None:
        await self._press(
            "controller_off_days_failed",
            self.coordinator.turn_controller_off_for_days(),
        )


BUTTON_ENTITY_CLASSES: dict[str, type[SolemButtonEntity]] = {
    "SPRINKLE_BUTTON": IrrigationStartButton,
    "STOP_BUTTON": IrrigationStopButton,
    "ON_BUTTON": ControllerOnButton,
    "OFF_BUTTON": ControllerOffButton,
    "OFF_DAYS_BUTTON": ControllerOffDaysButton,
}
