"""Base entity which all other entity platform classes can inherit.

As all entity types have a common set of properties, you can
create a base entity like this and inherit it in all your entity platforms.

This just makes your code more efficient and is totally optional.

See each entity platform (ie sensor.py, switch.py) for how this is inheritted
and what additional properties and methods you need to add for each entity type.

"""

import logging
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONTROLLER_MAC_ADDRESS
from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

_TRANSLATION_KEYS = {
    "BATTERY_SENSOR": "battery",
    "BATTERY_VOLTAGE_SENSOR": "battery_voltage",
    "BATTERY_LOW_SENSOR": "battery_low",
    "IRRIGATION_DURATION_NUMBER": "irrigation_manual_duration",
    "LAST_TIME_SYNC_SENSOR": "last_time_sync",
    "OFF_BUTTON": "controller_off",
    "ON_BUTTON": "controller_on",
    "PROGRAM_NAME_SENSOR": "program_name",
    "PROGRAM_NEXT_START_SENSOR": "program_next_start",
    "PROGRAM_SCHEDULE_SENSOR": "program_schedule",
    "REMAINING_SPRINKLE_SENSOR": "station_remaining_time",
    "SPRINKLE_BUTTON": "sprinkle_station",
    "STOP_BUTTON": "stop_sprinkle",
}


class SolemBaseEntity(CoordinatorEntity):
    """Base Entity Class.

    This inherits a CoordinatorEntity class to register your entites to be updated
    by your DataUpdateCoordinator when async_update_data is called, either on the scheduled
    interval or by forcing an update.
    """

    coordinator: SolemCoordinator

    # ----------------------------------------------------------------------------
    # Using attr_has_entity_name = True causes HA to name your entities with the
    # device name and entity name.  Ie if your name property of your entity is
    # Voltage and this entity belongs to a device, Lounge Socket, this will name
    # your entity to be sensor.lounge_socket_voltage
    #
    # It is highly recommended (by me) to use this to give a good name structure
    # to your entities.  However, totally optional.
    # ----------------------------------------------------------------------------
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SolemCoordinator, device: dict[str, Any], parameter: str
    ) -> None:
        """Initialise entity."""
        super().__init__(coordinator)
        self.device = device
        self.device_id = device["device_id"]
        self.parameter = parameter
        device_type = device["device_type"]
        self._attr_translation_key = _TRANSLATION_KEYS.get(device_type)
        if device_type == "STATE_SENSOR":
            self._attr_translation_key = (
                "station_status" if "_station_" in self.device_id else "controller_status"
            )
        self._attr_translation_placeholders = self._translation_placeholders()

    def _translation_placeholders(self) -> dict[str, str] | None:
        """Return translated-name placeholders for station and program entities."""
        name = self.device["device_name"]
        device_type = self.device["device_type"]
        if device_type == "STATE_SENSOR" and "_station_" in self.device_id:
            return {"station_name": name.removesuffix(" Status")}
        if device_type == "REMAINING_SPRINKLE_SENSOR":
            return {"station_name": name.removesuffix(" remaining time")}
        if device_type == "SPRINKLE_BUTTON":
            return {"station_name": name.removeprefix("Sprinkle ")}
        if device_type.startswith("PROGRAM_"):
            return {"program": self.device_id.rsplit("_", 2)[-2].upper()}
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update sensor with latest data from coordinator."""
        # This method is called by your DataUpdateCoordinator when a successful update runs.
        self.device = self.coordinator.get_device(self.device_id)
        if self.device is not None:
            self._attr_translation_placeholders = self._translation_placeholders()
        _LOGGER.debug(
            "Updating device: %s, %s",
            self.device_id,
            self.coordinator.get_device_parameter(self.device_id, "device_name"),
        )
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""

        # ----------------------------------------------------------------------------
        # Identifiers are what group entities into the same device.
        # If your device is created elsewhere, you can just specify the indentifiers
        # parameter to link an entity to a device.
        # If your device connects via another device, add via_device parameter with
        # the indentifiers of that device.
        #
        # Device identifiers should be unique, so use your integration name (DOMAIN)
        # and a device uuid, mac address or some other unique attribute.
        # ----------------------------------------------------------------------------
        return DeviceInfo(
            name=self.coordinator.controller_mac_address,
            manufacturer="Solem",
            model="BL-IP",
            sw_version=self.coordinator.firmware_version,
            identifiers={
                (
                    DOMAIN,
                    self.coordinator.controller_mac_address,
                )
            },
        )

    @property
    def icon(self) -> str:
        """Return the name of the sensor."""
        return self.device["icon"] if self.device else "mdi:help-circle"

    @property
    def unique_id(self) -> str:
        """Return unique id."""

        # ----------------------------------------------------------------------------
        # All entities must have a unique id across your whole Home Assistant server -
        # and that also goes for anyone using your integration who may have many other
        # integrations loaded.
        #
        # Think carefully what you want this to be as changing it later will cause HA
        # to create new entities.
        #
        # It is recommended to have your integration name (DOMAIN), some unique id
        # from your device such as a UUID, MAC address etc (not IP address) and then
        # something unique to your entity (like name - as this would be unique on a
        # device)
        #
        # If in your situation you have some hub that connects to devices which then
        # you want to create multiple sensors for each device, you would do something
        # like.
        #
        # f"{DOMAIN}-{HUB_MAC_ADDRESS}-{DEVICE_UID}-{ENTITY_NAME}""
        #
        # This is even more important if your integration supports multiple instances.
        # ----------------------------------------------------------------------------
        return f"{DOMAIN}-{self.coordinator.controller_mac_address}-{self.coordinator.get_device_parameter(self.device_id, 'device_uid')}-{self.parameter}"

    @property
    def extra_state_attributes(self):
        """Return the extra state attributes."""
        # Add any additional attributes you want on your sensor.
        attrs = {}
        return attrs
