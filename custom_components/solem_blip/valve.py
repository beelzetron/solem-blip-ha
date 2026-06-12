"""Valve platform for the Solem BL-IP integration."""

from __future__ import annotations

import logging
from collections.abc import Coroutine
from typing import Any

from homeassistant.components.valve import ValveEntity, ValveEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import APIConnectionError
from .config_entry import MyConfigEntry
from .const import DOMAIN
from .coordinator import SolemCoordinator
from .entity import SolemBaseEntity
from .entity_descriptions import VALVE_DESCRIPTIONS, SolemValveEntityDescription

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem BL-IP valves."""
    coordinator = config_entry.runtime_data.coordinator
    valves: list[SolemValveEntity] = []

    for device in coordinator.data:
        device_type = device.get("device_type")
        if not device_type or device_type not in VALVE_DESCRIPTIONS:
            continue
        description = VALVE_DESCRIPTIONS[device_type]
        entity_class = VALVE_ENTITY_CLASSES[device_type]
        valves.append(entity_class(coordinator, device, description))

    async_add_entities(valves)


class SolemValveEntity(SolemBaseEntity, ValveEntity):
    """Base valve entity for Solem BL-IP."""

    entity_description: SolemValveEntityDescription

    _attr_reports_position = False
    _attr_supported_features = (
        ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE | ValveEntityFeature.STOP
    )

    def __init__(
        self,
        coordinator: SolemCoordinator,
        device: dict[str, Any],
        description: SolemValveEntityDescription,
    ) -> None:
        """Initialise valve."""
        super().__init__(coordinator, device, None, description)

    async def _run_action(
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
            _LOGGER.error("Valve action failed: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders=translation_placeholders,
            ) from err


class StationValve(SolemValveEntity):
    """Manual irrigation valve for one station."""

    @property
    def station_num(self) -> int:
        """Return the station number represented by this valve."""
        return int(self.device["station_num"])

    @property
    def is_closed(self) -> bool:
        """Return whether this station is not currently watering."""
        return not (
            self.coordinator._is_watering
            and self.coordinator.active_station_num == self.station_num
        )

    async def async_open_valve(self) -> None:
        """Start manual irrigation on this station."""
        station = self.station_num
        await self._run_action(
            "start_irrigation_failed",
            self.coordinator.start_irrigation(station),
            translation_placeholders={"station": str(station)},
        )

    async def async_close_valve(self) -> None:
        """Stop active irrigation."""
        await self._stop_irrigation()

    async def async_stop_valve(self) -> None:
        """Stop active irrigation."""
        await self._stop_irrigation()

    async def _stop_irrigation(self) -> None:
        """Stop irrigation using the controller-wide stop command."""
        await self._run_action(
            "stop_irrigation_failed",
            self.coordinator.stop_irrigation(),
        )


VALVE_ENTITY_CLASSES: dict[str, type[SolemValveEntity]] = {
    "STATION_VALVE": StationValve,
}
