"""Binary sensor platform for the Solem BL-IP integration."""

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator


@dataclass
class BinaryTypeClass:
    """Map coordinator device types to binary sensor classes."""

    device_type: str
    state_field: str
    binary_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up Solem BL-IP binary sensors."""
    coordinator: SolemCoordinator = config_entry.runtime_data.coordinator

    binary_sensor_types = [
        BinaryTypeClass("BATTERY_LOW_SENSOR", "state", BatteryLow),
    ]

    binary_sensors = []
    for sensor_type in binary_sensor_types:
        binary_sensors.extend(
            [
                sensor_type.binary_class(coordinator, device, sensor_type.state_field)
                for device in coordinator.data
                if device.get("device_type") == sensor_type.device_type
            ]
        )

    async_add_entities(binary_sensors)


class BatteryLow(SolemBaseEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.BATTERY

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.battery_low
