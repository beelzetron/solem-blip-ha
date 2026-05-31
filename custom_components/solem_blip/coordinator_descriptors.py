"""Entity descriptor builders for SolemCoordinator."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .coordinator_polling import remaining_seconds_for_station
from .util import mac_to_uuid

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)


def build_controller_and_battery_descriptors(
    coordinator: SolemCoordinator,
    *,
    counter_start: int = 1,
) -> tuple[list[dict[str, Any]], int]:
    """Build controller status and battery entity descriptors."""
    data: list[dict[str, Any]] = []
    counter = counter_start

    data.append(
        {
            "device_id": coordinator.controller.device_id,
            "device_type": "STATE_SENSOR",
            "device_name": coordinator.controller.device_name,
            "device_uid": mac_to_uuid(coordinator.controller_mac_address, counter),
            "software_version": coordinator.controller.software_version,
            "state": coordinator.controller.state,
            "icon": coordinator.controller.icon,
            "last_reboot": coordinator.controller.last_reboot,
        }
    )
    counter += 1

    battery_percent = None
    if coordinator.battery_level is not None:
        battery_percent = round(coordinator.battery_level / 5 * 100)

    data.append(
        {
            "device_id": f"{coordinator.controller_mac_address}_battery",
            "device_type": "BATTERY_SENSOR",
            "device_name": "Battery",
            "device_uid": mac_to_uuid(coordinator.controller_mac_address, counter),
            "software_version": "1.0",
            "state": battery_percent,
            "icon": "mdi:battery",
            "last_reboot": None,
        }
    )
    counter += 1
    data.append(
        {
            "device_id": f"{coordinator.controller_mac_address}_battery_voltage",
            "device_type": "BATTERY_VOLTAGE_SENSOR",
            "device_name": "Battery voltage",
            "device_uid": mac_to_uuid(coordinator.controller_mac_address, counter),
            "software_version": "1.0",
            "state": (
                round(coordinator.battery_voltage / 10, 1)
                if coordinator.battery_voltage is not None
                else None
            ),
            "icon": "mdi:battery-outline",
            "last_reboot": None,
        }
    )
    counter += 1
    data.append(
        {
            "device_id": f"{coordinator.controller_mac_address}_battery_low",
            "device_type": "BATTERY_LOW_SENSOR",
            "device_name": "Battery low",
            "device_uid": mac_to_uuid(coordinator.controller_mac_address, counter),
            "software_version": "1.0",
            "state": coordinator.battery_low,
            "icon": "mdi:battery-alert",
            "last_reboot": None,
        }
    )
    counter += 1
    return data, counter


def build_station_descriptors(
    coordinator: SolemCoordinator,
    *,
    stations_counter: int = 801,
) -> list[dict[str, Any]]:
    """Build per-station state entity descriptors."""
    data: list[dict[str, Any]] = []
    for station_id in range(1, coordinator.num_stations + 1):
        station = coordinator.stations[station_id - 1]
        data.append(
            {
                "device_id": station.device_id,
                "device_type": "STATE_SENSOR",
                "device_name": station.device_name,
                "device_uid": mac_to_uuid(
                    coordinator.controller_mac_address, stations_counter
                ),
                "software_version": station.software_version,
                "state": station.state,
                "icon": station.icon,
                "last_reboot": station.last_reboot,
            }
        )
        stations_counter += 1
    return data


def build_remaining_time_descriptors(
    coordinator: SolemCoordinator,
    *,
    remaining_counter: int = 601,
) -> list[dict[str, Any]]:
    """Build per-station remaining sprinkle time descriptors."""
    data: list[dict[str, Any]] = []
    for station_id in range(1, coordinator.num_stations + 1):
        data.append(
            {
                "device_id": f"{coordinator.controller_mac_address}_remaining_sprinkle_station_{station_id}",
                "device_type": "REMAINING_SPRINKLE_SENSOR",
                "device_name": f"{coordinator._station_name(station_id)} remaining time",
                "device_uid": mac_to_uuid(
                    coordinator.controller_mac_address, remaining_counter
                ),
                "software_version": "1.0",
                "state": remaining_seconds_for_station(coordinator, station_id),
                "icon": "mdi:timer-outline",
                "last_reboot": None,
            }
        )
        remaining_counter += 1
    return data


def build_control_descriptors(
    coordinator: SolemCoordinator,
    *,
    counter: int,
    buttons_counter: int = 901,
) -> list[dict[str, Any]]:
    """Build manual duration, sprinkle buttons, and controller on/off descriptors."""
    data: list[dict[str, Any]] = []

    data.append(
        {
            "device_id": f"{coordinator.controller_mac_address}_irrigation_manual_duration",
            "device_type": "IRRIGATION_DURATION_NUMBER",
            "device_name": "Irrigation Manual Duration",
            "device_uid": mac_to_uuid(coordinator.controller_mac_address, counter),
            "software_version": "1.0",
            "value": coordinator.irrigation_manual_duration,
            "icon": "mdi:clock-time-five-outline",
            "last_reboot": None,
        }
    )
    counter += 1

    for station_id in range(1, coordinator.num_stations + 1):
        data.append(
            {
                "device_id": f"{coordinator.controller_mac_address}_irrigation_manual_start_station_{station_id}",
                "device_type": "SPRINKLE_BUTTON",
                "device_name": f"Sprinkle {coordinator._station_name(station_id)}",
                "device_uid": mac_to_uuid(
                    coordinator.controller_mac_address, buttons_counter
                ),
                "software_version": "1.0",
                "icon": "mdi:sprinkler",
                "last_reboot": None,
            }
        )
        buttons_counter += 1

    data.extend(
        [
            {
                "device_id": f"{coordinator.controller_mac_address}_irrigation_stop",
                "device_type": "STOP_BUTTON",
                "device_name": "Stop sprinkle",
                "device_uid": mac_to_uuid(coordinator.controller_mac_address, counter),
                "software_version": "1.0",
                "icon": "mdi:water-off",
                "last_reboot": None,
            },
            {
                "device_id": f"{coordinator.controller_mac_address}_irrigation_controller_on",
                "device_type": "ON_BUTTON",
                "device_name": "Turn on controller",
                "device_uid": mac_to_uuid(
                    coordinator.controller_mac_address, counter + 1
                ),
                "software_version": "1.0",
                "icon": "mdi:power-on",
                "last_reboot": None,
            },
            {
                "device_id": f"{coordinator.controller_mac_address}_irrigation_controller_off",
                "device_type": "OFF_BUTTON",
                "device_name": "Turn off controller",
                "device_uid": mac_to_uuid(
                    coordinator.controller_mac_address, counter + 2
                ),
                "software_version": "1.0",
                "icon": "mdi:power-off",
                "last_reboot": None,
            },
        ]
    )
    return data


def build_all_descriptors(coordinator: SolemCoordinator) -> list[dict[str, Any]]:
    """Compose the full entity descriptor list from coordinator state."""
    data, counter = build_controller_and_battery_descriptors(coordinator)
    data.extend(build_station_descriptors(coordinator))
    data.extend(build_remaining_time_descriptors(coordinator))
    data.extend(build_control_descriptors(coordinator, counter=counter))
    _LOGGER.debug("%s - Updated sensors.", coordinator.controller_mac_address)
    return data
