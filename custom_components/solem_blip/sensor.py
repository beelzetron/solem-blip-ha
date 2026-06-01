"""Sensor platform for the Solem BL-IP integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entry import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator
from .coordinator_polling import active_program_name
from .entity_descriptions import SENSOR_DESCRIPTIONS, SolemSensorEntityDescription

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem BL-IP sensors."""
    coordinator = config_entry.runtime_data.coordinator
    sensors: list[SolemSensorEntity] = []

    for device in coordinator.data:
        device_type = device.get("device_type")
        if not device_type or device_type not in SENSOR_DESCRIPTIONS:
            continue
        description = SENSOR_DESCRIPTIONS[device_type]
        entity_class = SENSOR_ENTITY_CLASSES.get(device_type, SolemSensorEntity)
        sensors.append(
            entity_class(coordinator, device, description.state_field, description)
        )

    async_add_entities(sensors)


class SolemSensorEntity(SolemBaseEntity, SensorEntity):
    """Generic Solem BL-IP sensor entity."""

    entity_description: SolemSensorEntityDescription

    def __init__(
        self,
        coordinator: SolemCoordinator,
        device: dict[str, Any],
        parameter: str,
        description: SolemSensorEntityDescription,
    ) -> None:
        """Initialise sensor."""
        super().__init__(coordinator, device, parameter, description)


class StateSensor(SolemSensorEntity):
    """Controller or station status sensor."""

    @property
    def native_value(self) -> int | float | str | None:
        return cast(
            int | float | str | None, self._descriptor_field()
        )

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        if "_irrigation_controller_" not in self.device_id:
            return {}
        attributes: dict[str, int | str | None] = {}
        if self.coordinator.active_program_num is not None:
            attributes["active_program"] = self.coordinator.active_program_num
            attributes["active_program_name"] = active_program_name(self.coordinator)
        if self.coordinator.watering_origin is not None:
            attributes["watering_origin"] = self.coordinator.watering_origin
        return attributes


class BatterySensor(SolemSensorEntity):
    """Battery percentage sensor."""

    @property
    def native_value(self) -> int | None:
        return cast(int | None, self._descriptor_field())

    @property
    def extra_state_attributes(self) -> dict[str, int | bool | None]:
        return {
            "battery_level": self.coordinator.battery_level,
            "battery_voltage_raw": self.coordinator.battery_voltage,
        }


class BatteryVoltageSensor(SolemSensorEntity):
    """Battery voltage diagnostic sensor."""

    @property
    def native_value(self) -> float | None:
        return cast(float | None, self._descriptor_field())


class RemainingSprinkleSensor(SolemSensorEntity):
    """Per-station remaining sprinkle time sensor."""

    @property
    def native_value(self) -> int | None:
        return cast(int | None, self._descriptor_field())


class LastTimeSyncSensor(SolemSensorEntity):
    """Last RTC sync timestamp sensor."""

    @property
    def native_value(self) -> datetime | None:
        return cast(datetime | None, self._descriptor_field())


class ProgramSensor(SolemSensorEntity):
    """Base class for sensors refreshed by the slower schedule coordinator."""

    async def async_added_to_hass(self) -> None:
        """Subscribe to schedule changes and start the first background read."""
        await super().async_added_to_hass()
        schedule_coordinator = self.coordinator.schedule_coordinator
        self.async_on_remove(
            schedule_coordinator.async_add_listener(self._handle_coordinator_update)
        )
        schedule_coordinator.async_start_first_refresh()


class ProgramNextStartSensor(ProgramSensor):
    """On-device program next start sensor."""

    @property
    def native_value(self) -> datetime | None:
        return cast(datetime | None, self._descriptor_field())

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        device = self.coordinator.get_device(self.device_id) or {}
        return dict(device.get("attributes") or {})


class ProgramScheduleSensor(ProgramSensor):
    """On-device program schedule summary sensor."""

    @property
    def native_value(self) -> int | None:
        return cast(int | None, self._descriptor_field())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        device = self.coordinator.get_device(self.device_id) or {}
        return dict(device.get("attributes") or {})


SENSOR_ENTITY_CLASSES: dict[str, type[SolemSensorEntity]] = {
    "STATE_SENSOR": StateSensor,
    "BATTERY_SENSOR": BatterySensor,
    "BATTERY_VOLTAGE_SENSOR": BatteryVoltageSensor,
    "REMAINING_SPRINKLE_SENSOR": RemainingSprinkleSensor,
    "LAST_TIME_SYNC_SENSOR": LastTimeSyncSensor,
    "PROGRAM_NEXT_START_SENSOR": ProgramNextStartSensor,
    "PROGRAM_SCHEDULE_SENSOR": ProgramScheduleSensor,
}
