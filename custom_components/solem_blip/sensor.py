"""Sensor platform for the Solem BL-IP integration."""

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator


@dataclass
class SensorTypeClass:
    """Map coordinator device types to sensor classes."""

    device_type: str
    state_field: str
    sensor_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up Solem BL-IP sensors."""
    coordinator: SolemCoordinator = config_entry.runtime_data.coordinator

    sensor_types = [
        SensorTypeClass("STATE_SENSOR", "state", StateSensor),
        SensorTypeClass("BATTERY_SENSOR", "state", BatterySensor),
        SensorTypeClass("BATTERY_VOLTAGE_SENSOR", "state", BatteryVoltageSensor),
        SensorTypeClass("REMAINING_SPRINKLE_SENSOR", "state", RemainingSprinkleSensor),
        SensorTypeClass("LAST_TIME_SYNC_SENSOR", "state", LastTimeSyncSensor),
        SensorTypeClass("PROGRAM_NAME_SENSOR", "state", ProgramNameSensor),
        SensorTypeClass("PROGRAM_NEXT_START_SENSOR", "state", ProgramNextStartSensor),
        SensorTypeClass("PROGRAM_SCHEDULE_SENSOR", "state", ProgramScheduleSensor),
    ]

    sensors = []
    for sensor_type in sensor_types:
        sensors.extend(
            [
                sensor_type.sensor_class(coordinator, device, sensor_type.state_field)
                for device in coordinator.data
                if device.get("device_type") == sensor_type.device_type
            ]
        )

    async_add_entities(sensors)


class StateSensor(SolemBaseEntity, SensorEntity):
    @property
    def native_value(self) -> int | float | str:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)


class BatterySensor(SolemBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> int | None:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)

    @property
    def extra_state_attributes(self) -> dict[str, int | bool | None]:
        return {
            "battery_level": self.coordinator.battery_level,
            "battery_voltage_raw": self.coordinator.battery_voltage,
        }


class BatteryVoltageSensor(SolemBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)


class RemainingSprinkleSensor(SolemBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    @property
    def native_value(self) -> int | None:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)


class LastTimeSyncSensor(SolemBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)


class ProgramNameSensor(SolemBaseEntity, SensorEntity):
    @property
    def native_value(self) -> str | None:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)


class ProgramNextStartSensor(SolemBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        device = self.coordinator.get_device(self.device_id) or {}
        attributes = device.get("attributes") or {}
        return {
            "minutes_since_midnight": attributes.get("minutes_since_midnight"),
        }


class ProgramScheduleSensor(SolemBaseEntity, SensorEntity):
    @property
    def native_value(self) -> int | None:
        return self.coordinator.get_device_parameter(self.device_id, self.parameter)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        device = self.coordinator.get_device(self.device_id) or {}
        return dict(device.get("attributes") or {})
