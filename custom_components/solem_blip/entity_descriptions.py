"""Entity descriptions for the Solem BL-IP integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfTime
from homeassistant.helpers.entity import EntityCategory

from .const import MAX_CONTROLLER_OFF_DAYS


@dataclass(frozen=True, kw_only=True)
class SolemSensorEntityDescription(SensorEntityDescription):
    """Sensor entity description with coordinator device type mapping."""

    device_type: str
    state_field: str = "state"


@dataclass(frozen=True, kw_only=True)
class SolemBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Binary sensor entity description with coordinator device type mapping."""

    device_type: str
    state_field: str = "state"


@dataclass(frozen=True, kw_only=True)
class SolemButtonEntityDescription(ButtonEntityDescription):
    """Button entity description with coordinator device type mapping."""

    device_type: str


@dataclass(frozen=True, kw_only=True)
class SolemNumberEntityDescription(NumberEntityDescription):
    """Number entity description with coordinator device type mapping."""

    device_type: str
    state_field: str = "value"


SENSOR_DESCRIPTIONS: dict[str, SolemSensorEntityDescription] = {
    "STATE_SENSOR": SolemSensorEntityDescription(
        key="state",
        device_type="STATE_SENSOR",
        has_entity_name=True,
    ),
    "BATTERY_SENSOR": SolemSensorEntityDescription(
        key="battery",
        device_type="BATTERY_SENSOR",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        has_entity_name=True,
    ),
    "BATTERY_VOLTAGE_SENSOR": SolemSensorEntityDescription(
        key="battery_voltage",
        device_type="BATTERY_VOLTAGE_SENSOR",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
        has_entity_name=True,
    ),
    "REMAINING_SPRINKLE_SENSOR": SolemSensorEntityDescription(
        key="station_remaining_time",
        device_type="REMAINING_SPRINKLE_SENSOR",
        translation_key="station_remaining_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        has_entity_name=True,
    ),
    "LAST_TIME_SYNC_SENSOR": SolemSensorEntityDescription(
        key="last_time_sync",
        device_type="LAST_TIME_SYNC_SENSOR",
        translation_key="last_time_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        has_entity_name=True,
    ),
    "PROGRAM_NEXT_START_SENSOR": SolemSensorEntityDescription(
        key="program_next_start",
        device_type="PROGRAM_NEXT_START_SENSOR",
        translation_key="program_next_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        has_entity_name=True,
    ),
    "PROGRAM_SCHEDULE_SENSOR": SolemSensorEntityDescription(
        key="program_schedule",
        device_type="PROGRAM_SCHEDULE_SENSOR",
        translation_key="program_schedule",
        has_entity_name=True,
    ),
}

BINARY_SENSOR_DESCRIPTIONS: dict[str, SolemBinarySensorEntityDescription] = {
    "BATTERY_LOW_SENSOR": SolemBinarySensorEntityDescription(
        key="battery_low",
        device_type="BATTERY_LOW_SENSOR",
        translation_key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        has_entity_name=True,
    ),
    "PROGRAM_RUNNING_SENSOR": SolemBinarySensorEntityDescription(
        key="program_running",
        device_type="PROGRAM_RUNNING_SENSOR",
        translation_key="program_running",
        entity_category=EntityCategory.DIAGNOSTIC,
        has_entity_name=True,
    ),
}

BUTTON_DESCRIPTIONS: dict[str, SolemButtonEntityDescription] = {
    "SPRINKLE_BUTTON": SolemButtonEntityDescription(
        key="sprinkle_station",
        device_type="SPRINKLE_BUTTON",
        translation_key="sprinkle_station",
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
    ),
    "STOP_BUTTON": SolemButtonEntityDescription(
        key="stop_sprinkle",
        device_type="STOP_BUTTON",
        translation_key="stop_sprinkle",
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
    ),
    "ON_BUTTON": SolemButtonEntityDescription(
        key="controller_on",
        device_type="ON_BUTTON",
        translation_key="controller_on",
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
    ),
    "OFF_BUTTON": SolemButtonEntityDescription(
        key="controller_off",
        device_type="OFF_BUTTON",
        translation_key="controller_off",
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
    ),
    "OFF_DAYS_BUTTON": SolemButtonEntityDescription(
        key="controller_off_days",
        device_type="OFF_DAYS_BUTTON",
        translation_key="controller_off_days",
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
    ),
}

NUMBER_DESCRIPTIONS: dict[str, SolemNumberEntityDescription] = {
    "IRRIGATION_DURATION_NUMBER": SolemNumberEntityDescription(
        key="irrigation_manual_duration",
        device_type="IRRIGATION_DURATION_NUMBER",
        translation_key="irrigation_manual_duration",
        entity_category=EntityCategory.CONFIG,
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        has_entity_name=True,
    ),
    "CONTROLLER_OFF_DAYS_NUMBER": SolemNumberEntityDescription(
        key="controller_off_days",
        device_type="CONTROLLER_OFF_DAYS_NUMBER",
        translation_key="controller_off_days",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=MAX_CONTROLLER_OFF_DAYS,
        native_step=1,
        has_entity_name=True,
    ),
}
