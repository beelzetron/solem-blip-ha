"""DataUpdateCoordinator for the Solem BL-IP integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import sleep
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from solem_blip_ble import SolemClient

from .api import APIConnectionError
from .bluetooth import async_get_connectable_device, async_wait_for_connectable_device
from .const import (
    BLUETOOTH_DEFAULT_TIMEOUT,
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_MANUAL_DURATION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NUM_STATIONS,
    SOLEM_API_MOCK,
)
from .models import IrrigationController, IrrigationStation
from .util import mac_to_uuid

_LOGGER = logging.getLogger(__name__)


class SolemCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Poll BLE status and expose manual irrigation controls."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self.controller_mac_address = config_entry.data[CONTROLLER_MAC_ADDRESS].rsplit(
            " - ", 1
        )[1]
        _LOGGER.info(
            "%s - Starting coordinator initialization...",
            self.controller_mac_address,
        )

        self.poll_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self.bluetooth_timeout = config_entry.options.get(
            BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT
        )
        self.solem_api_mock = (
            config_entry.options.get(SOLEM_API_MOCK, "false") == "true"
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
        )

        self.num_stations = config_entry.data.get(NUM_STATIONS, 2)
        self.config_entry = config_entry

        self.controller = IrrigationController(
            device_id=f"{self.controller_mac_address}_irrigation_controller_status",
            device_name="Controller Status",
            device_uid="",
            software_version="1.0",
            icon="mdi:state-machine",
        )
        self.stations = [
            IrrigationStation(
                device_id=f"{self.controller_mac_address}_irrigation_station_{station_id}_status",
                device_name=f"Station {station_id} Status",
                device_uid="",
                station_number=station_id,
                software_version="1.0",
                icon="mdi:state-machine",
            )
            for station_id in range(1, self.num_stations + 1)
        ]

        self.api = SolemClient(
            self.controller_mac_address,
            bluetooth_timeout=self.bluetooth_timeout,
            mock=self.solem_api_mock,
            max_station_num=self.num_stations,
            ble_device_resolver=lambda: async_get_connectable_device(
                hass, self.controller_mac_address
            ),
        )

        self.irrigation_stop_event = asyncio.Event()
        self._irrigation_active = False
        self._irrigation_monitor_task: asyncio.Task[None] | None = None
        self._ready = False
        self.battery_voltage: int | None = None
        self.battery_level: int | None = None
        self.battery_low = False
        self.irrigation_manual_duration = DEFAULT_MANUAL_DURATION
        self.remaining_seconds: int | None = None
        self.active_station_num: int | None = None

        _LOGGER.info(
            "%s - Coordinator initialization finished.",
            self.controller_mac_address,
        )

    async def update_config(self, config_entry: ConfigEntry) -> None:
        """Apply a reconfigured config entry."""
        _LOGGER.info(
            "%s - Updating coordinator with new config...",
            self.controller_mac_address,
        )
        self.config_entry = config_entry
        self.controller_mac_address = config_entry.data[CONTROLLER_MAC_ADDRESS].rsplit(
            " - ", 1
        )[1]
        self.poll_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self.bluetooth_timeout = config_entry.options.get(
            BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT
        )
        self.solem_api_mock = (
            config_entry.options.get(SOLEM_API_MOCK, "false") == "true"
        )
        self.update_interval = timedelta(seconds=self.poll_interval)
        self.num_stations = config_entry.data.get(NUM_STATIONS, 2)

        self.api = SolemClient(
            self.controller_mac_address,
            bluetooth_timeout=self.bluetooth_timeout,
            mock=self.solem_api_mock,
            max_station_num=self.num_stations,
            ble_device_resolver=lambda: async_get_connectable_device(
                self.hass, self.controller_mac_address
            ),
        )

        self.stations = [
            IrrigationStation(
                device_id=f"{self.controller_mac_address}_irrigation_station_{station_id}_status",
                device_name=f"Station {station_id} Status",
                device_uid="",
                station_number=station_id,
                software_version="1.0",
                icon="mdi:state-machine",
            )
            for station_id in range(1, self.num_stations + 1)
        ]

        await self.async_request_refresh()
        _LOGGER.info(
            "%s - Updated coordinator with new config.",
            self.controller_mac_address,
        )

    async def async_shutdown(self) -> None:
        """Release BLE resources when the integration unloads."""
        self.irrigation_stop_event.set()
        task = self._irrigation_monitor_task
        if task is not None and not task.done():
            task.cancel()
        await self._await_irrigation_monitor_task()
        self._clear_irrigation_idle_state()
        await self.api.disconnect()

    async def async_init(self) -> None:
        """Connect to the controller and build initial entity data."""
        if not self.solem_api_mock:
            await async_wait_for_connectable_device(
                self.hass, self.controller_mac_address
            )
            _LOGGER.info(
                "%s - Attempting initial BLE connection...",
                self.controller_mac_address,
            )
            try:
                await self.api.connect()
                _LOGGER.info(
                    "%s - Connected to Solem BLE device",
                    self.controller_mac_address,
                )
            except Exception as ex:
                _LOGGER.warning(
                    "%s - Initial BLE connect failed; will retry on poll: %s",
                    self.controller_mac_address,
                    ex,
                )

        self._ready = True
        self.async_set_updated_data(await self.async_update_all_sensors())

    def _apply_status(self, status: dict[str, Any]) -> None:
        """Update coordinator state from a BLE status dict."""
        self.controller.state = status.get("controller_state", "Unknown")
        self.battery_voltage = status.get("battery_voltage")
        self.battery_level = status.get("battery_level")
        self.battery_low = bool(status.get("battery_low", False))

        if status.get("is_watering") and status.get("station_num"):
            active_station_num = status["station_num"]
            self.active_station_num = active_station_num
            self.remaining_seconds = status.get("remaining_seconds")
            if 1 <= active_station_num <= self.num_stations:
                for index, station in enumerate(self.stations):
                    station.state = (
                        "Sprinkling"
                        if index + 1 == active_station_num
                        else "Stopped"
                    )
            else:
                _LOGGER.warning(
                    "%s - Watering on station %s outside configured range 1-%s",
                    self.controller_mac_address,
                    active_station_num,
                    self.num_stations,
                )
        elif not status.get("is_watering"):
            self.active_station_num = None
            self.remaining_seconds = None
            for station in self.stations:
                station.state = "Stopped"
        else:
            _LOGGER.warning(
                "%s - Watering active but no station in status; keeping existing states",
                self.controller_mac_address,
            )

        _LOGGER.debug(
            "%s - Status: controller=%s watering=%s station=%s remaining=%ss battery=%s (%s/5)",
            self.controller_mac_address,
            self.controller.state,
            status.get("is_watering"),
            status.get("station_num"),
            self.remaining_seconds,
            self.battery_voltage,
            self.battery_level,
        )

    async def _fetch_device_status(self) -> dict[str, Any]:
        """Poll device and update controller/station states from BLE status."""
        status = await self.api.get_status()
        self._apply_status(status)
        return status

    def _clear_irrigation_idle_state(self) -> None:
        """Reset coordinator state after irrigation stops or fails to start."""
        self._irrigation_active = False
        self.active_station_num = None
        self.remaining_seconds = None
        for station in self.stations:
            station.state = "Stopped"

    def _clear_monitor_task_ref(self, task: asyncio.Task[None]) -> None:
        """Clear stored monitor task when it completes."""
        if self._irrigation_monitor_task is task:
            self._irrigation_monitor_task = None

    async def _await_irrigation_monitor_task(self) -> None:
        """Wait for the background irrigation monitor to finish."""
        task = self._irrigation_monitor_task
        if task is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _remaining_seconds_for_station(self, station_id: int) -> int | None:
        """Return remaining sprinkle seconds for a station (0 when idle/inactive)."""
        if (
            self.active_station_num == station_id
            and self.remaining_seconds is not None
        ):
            return self.remaining_seconds
        if self.active_station_num == station_id and self.remaining_seconds is None:
            return None
        return 0

    async def async_update_all_sensors(self) -> list[dict[str, Any]]:
        """Build entity descriptor list from current coordinator state."""
        if not self._irrigation_active:
            try:
                await self._fetch_device_status()
            except APIConnectionError as ex:
                _LOGGER.warning(
                    "%s - Failed to get device status, keeping last known states: %s",
                    self.controller_mac_address,
                    ex,
                )
            except Exception as ex:
                _LOGGER.error(
                    "%s - Unexpected error getting status, keeping last known states: %s",
                    self.controller_mac_address,
                    ex,
                    exc_info=True,
                )

        data: list[dict[str, Any]] = []
        counter = 1
        stations_counter = 801
        remaining_counter = 601
        buttons_counter = 901

        data.append(
            {
                "device_id": self.controller.device_id,
                "device_type": "STATE_SENSOR",
                "device_name": self.controller.device_name,
                "device_uid": mac_to_uuid(self.controller_mac_address, counter),
                "software_version": self.controller.software_version,
                "state": self.controller.state,
                "icon": self.controller.icon,
                "last_reboot": self.controller.last_reboot,
            }
        )
        counter += 1

        battery_percent = None
        if self.battery_level is not None:
            battery_percent = round(self.battery_level / 5 * 100)

        data.append(
            {
                "device_id": f"{self.controller_mac_address}_battery",
                "device_type": "BATTERY_SENSOR",
                "device_name": "Battery",
                "device_uid": mac_to_uuid(self.controller_mac_address, counter),
                "software_version": "1.0",
                "state": battery_percent,
                "icon": "mdi:battery",
                "last_reboot": None,
            }
        )
        counter += 1
        data.append(
            {
                "device_id": f"{self.controller_mac_address}_battery_voltage",
                "device_type": "BATTERY_VOLTAGE_SENSOR",
                "device_name": "Battery voltage",
                "device_uid": mac_to_uuid(self.controller_mac_address, counter),
                "software_version": "1.0",
                "state": (
                    round(self.battery_voltage / 10, 1)
                    if self.battery_voltage is not None
                    else None
                ),
                "icon": "mdi:battery-outline",
                "last_reboot": None,
            }
        )
        counter += 1
        data.append(
            {
                "device_id": f"{self.controller_mac_address}_battery_low",
                "device_type": "BATTERY_LOW_SENSOR",
                "device_name": "Battery low",
                "device_uid": mac_to_uuid(self.controller_mac_address, counter),
                "software_version": "1.0",
                "state": self.battery_low,
                "icon": "mdi:battery-alert",
                "last_reboot": None,
            }
        )
        counter += 1

        for station_id in range(1, self.num_stations + 1):
            station = self.stations[station_id - 1]
            data.append(
                {
                    "device_id": station.device_id,
                    "device_type": "STATE_SENSOR",
                    "device_name": station.device_name,
                    "device_uid": mac_to_uuid(
                        self.controller_mac_address, stations_counter
                    ),
                    "software_version": station.software_version,
                    "state": station.state,
                    "icon": station.icon,
                    "last_reboot": station.last_reboot,
                }
            )
            stations_counter += 1

        for station_id in range(1, self.num_stations + 1):
            data.append(
                {
                    "device_id": f"{self.controller_mac_address}_remaining_sprinkle_station_{station_id}",
                    "device_type": "REMAINING_SPRINKLE_SENSOR",
                    "device_name": f"Station {station_id} remaining time",
                    "device_uid": mac_to_uuid(
                        self.controller_mac_address, remaining_counter
                    ),
                    "software_version": "1.0",
                    "state": self._remaining_seconds_for_station(station_id),
                    "icon": "mdi:timer-outline",
                    "last_reboot": None,
                }
            )
            remaining_counter += 1

        data.append(
            {
                "device_id": f"{self.controller_mac_address}_irrigation_manual_duration",
                "device_type": "IRRIGATION_DURATION_NUMBER",
                "device_name": "Irrigation Manual Duration",
                "device_uid": mac_to_uuid(self.controller_mac_address, counter),
                "software_version": "1.0",
                "value": self.irrigation_manual_duration,
                "icon": "mdi:clock-time-five-outline",
                "last_reboot": None,
            }
        )
        counter += 1

        for station_id in range(1, self.num_stations + 1):
            data.append(
                {
                    "device_id": f"{self.controller_mac_address}_irrigation_manual_start_station_{station_id}",
                    "device_type": "SPRINKLE_BUTTON",
                    "device_name": f"Sprinkle station {station_id}",
                    "device_uid": mac_to_uuid(
                        self.controller_mac_address, buttons_counter
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
                    "device_id": f"{self.controller_mac_address}_irrigation_stop",
                    "device_type": "STOP_BUTTON",
                    "device_name": "Stop sprinkle",
                    "device_uid": mac_to_uuid(self.controller_mac_address, counter),
                    "software_version": "1.0",
                    "icon": "mdi:water-off",
                    "last_reboot": None,
                },
                {
                    "device_id": f"{self.controller_mac_address}_irrigation_controller_on",
                    "device_type": "ON_BUTTON",
                    "device_name": "Turn on controller",
                    "device_uid": mac_to_uuid(self.controller_mac_address, counter + 1),
                    "software_version": "1.0",
                    "icon": "mdi:power-on",
                    "last_reboot": None,
                },
                {
                    "device_id": f"{self.controller_mac_address}_irrigation_controller_off",
                    "device_type": "OFF_BUTTON",
                    "device_name": "Turn off controller",
                    "device_uid": mac_to_uuid(self.controller_mac_address, counter + 2),
                    "software_version": "1.0",
                    "icon": "mdi:power-off",
                    "last_reboot": None,
                },
            ]
        )

        _LOGGER.debug("%s - Updated sensors.", self.controller_mac_address)
        return data

    async def async_update_data(self) -> list[dict[str, Any]]:
        try:
            return await self.async_update_all_sensors()
        except Exception as err:
            _LOGGER.error(
                "%s - Coordinator update failed: %s",
                self.controller_mac_address,
                err,
                exc_info=True,
            )
            return self.data or []

    async def start_irrigation(
        self, station: int, minutes: int | None = None
    ) -> None:
        """Send a start command, then monitor watering in the background."""
        if self._irrigation_active:
            raise APIConnectionError("Irrigation is already in progress")

        duration = int(
            minutes if minutes is not None else self.irrigation_manual_duration
        )
        _LOGGER.info(
            "%s - Starting watering on station %s for %s minutes...",
            self.controller_mac_address,
            station,
            duration,
        )

        self.irrigation_stop_event.clear()
        self._irrigation_active = True
        self.stations[station - 1].state = "Sprinkling"
        self.async_set_updated_data(await self.async_update_all_sensors())

        self._irrigation_monitor_task = self.hass.async_create_task(
            self._run_irrigation_monitor(station, duration),
            name=f"{DOMAIN} irrigation station {station}",
        )
        self._irrigation_monitor_task.add_done_callback(self._clear_monitor_task_ref)

        try:
            await self.api.sprinkle_station_x_for_y_minutes(station, duration)
        except APIConnectionError as ex:
            _LOGGER.error(
                "%s - Failed to start irrigation due to connection error.",
                self.controller_mac_address,
                exc_info=True,
            )
            self._clear_irrigation_idle_state()
            raise HomeAssistantError(str(ex)) from ex
        except Exception as ex:
            _LOGGER.error(
                "%s - Failed to start irrigation due to error: %s",
                self.controller_mac_address,
                ex,
                exc_info=True,
            )
            self._clear_irrigation_idle_state()
            raise

    async def _run_irrigation_monitor(self, station: int, duration: int) -> None:
        """Monitor active watering until completion, stop, or safety timeout."""
        try:
            await self._monitor_irrigation_until_complete(station, duration)
            data = await self.async_update_all_sensors()
            self.async_set_updated_data(data)
        except asyncio.CancelledError:
            self._clear_irrigation_idle_state()
            raise
        except Exception as err:
            _LOGGER.error(
                "%s - Irrigation monitor failed for station %s: %s",
                self.controller_mac_address,
                station,
                err,
                exc_info=True,
            )

    async def _monitor_irrigation_until_complete(
        self, station: int, duration: int
    ) -> None:
        """Poll BLE status until watering stops or safety timeout."""
        poll_interval = max(2, min(self.poll_interval, 10))
        max_seconds = duration * 60 + 30
        elapsed = 0.0

        try:
            while elapsed < max_seconds:
                if self.irrigation_stop_event.is_set():
                    _LOGGER.info(
                        "%s - Irrigation cancellation triggered.",
                        self.controller_mac_address,
                    )
                    break

                await sleep(poll_interval)
                elapsed += poll_interval

                try:
                    status = await self.api.get_status()
                except APIConnectionError as ex:
                    _LOGGER.warning(
                        "%s - Status poll failed during irrigation: %s",
                        self.controller_mac_address,
                        ex,
                    )
                    continue

                self._apply_status(status)
                if not status.get("is_watering"):
                    _LOGGER.info(
                        "%s - Device reports watering finished.",
                        self.controller_mac_address,
                    )
                    break

                self.async_set_updated_data(await self.async_update_all_sensors())
            else:
                _LOGGER.warning(
                    "%s - Irrigation monitor hit safety timeout after %ss",
                    self.controller_mac_address,
                    max_seconds,
                )
        finally:
            self._clear_irrigation_idle_state()
            _LOGGER.info(
                "%s - Finished watering on station %s.",
                self.controller_mac_address,
                station,
            )

    async def stop_irrigation(self) -> None:
        _LOGGER.info("%s - Stopping watering...", self.controller_mac_address)
        try:
            await self.api.stop_manual_sprinkle()
        except APIConnectionError as ex:
            _LOGGER.error(
                "%s - Failed to stop irrigation due to connection error.",
                self.controller_mac_address,
            )
            raise HomeAssistantError(str(ex)) from ex

        self.irrigation_stop_event.set()
        await self._await_irrigation_monitor_task()
        self._clear_irrigation_idle_state()

        _LOGGER.info("%s - Stopped watering.", self.controller_mac_address)
        self.async_set_updated_data(await self.async_update_all_sensors())

    async def turn_controller_on(self) -> None:
        _LOGGER.info(
            "%s - Turning irrigation controller on...",
            self.controller_mac_address,
        )
        try:
            await self.api.turn_on()
        except APIConnectionError as ex:
            _LOGGER.error(
                "%s - Failed to turn controller on due to connection error.",
                self.controller_mac_address,
            )
            raise HomeAssistantError(str(ex)) from ex

        self.controller.state = "On"
        self.async_set_updated_data(await self.async_update_all_sensors())
        _LOGGER.info(
            "%s - Irrigation controller turned on.",
            self.controller_mac_address,
        )

    async def turn_controller_off(self) -> None:
        _LOGGER.info(
            "%s - Turning irrigation controller off...",
            self.controller_mac_address,
        )
        try:
            await self.api.turn_off_permanent()
        except APIConnectionError as ex:
            _LOGGER.error(
                "%s - Failed to turn controller off due to connection error.",
                self.controller_mac_address,
            )
            raise HomeAssistantError(str(ex)) from ex

        self.controller.state = "Off"
        self.async_set_updated_data(await self.async_update_all_sensors())
        _LOGGER.info(
            "%s - Irrigation controller turned off.",
            self.controller_mac_address,
        )

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Return one entity descriptor from coordinator data."""
        if not self.data:
            return None
        for device in self.data:
            if device["device_id"] == device_id:
                return device
        return None

    def get_device_parameter(self, device_id: str, parameter: str) -> Any:
        """Return one field from an entity descriptor."""
        if device := self.get_device(device_id):
            return device.get(parameter)
        return None
