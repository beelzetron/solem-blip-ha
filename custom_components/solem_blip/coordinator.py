"""DataUpdateCoordinator for the Solem BL-IP integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from solem_blip_ble import IrrigationProgram, SolemClient

from .config_entry import MyConfigEntry
from .const import (
    BLUETOOTH_DEFAULT_TIMEOUT,
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_MANUAL_DURATION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IRRIGATION_CONFIG_UPDATE_INTERVAL,
    NUM_STATIONS,
    PROGRAM_LABELS,
    SOLEM_API_MOCK,
)
from .coordinator_descriptors import build_all_descriptors
from .coordinator_irrigation import (
    await_irrigation_monitor_task,
    clear_irrigation_idle_state,
    clear_monitor_task_ref,
    run_irrigation_monitor,
    start_irrigation as irrigation_start,
    stop_irrigation as irrigation_stop,
    turn_controller_off as irrigation_turn_off,
    turn_controller_on as irrigation_turn_on,
)
from .coordinator_polling import (
    apply_status,
    fetch_device_metadata,
    fetch_device_status,
    fetch_irrigation_config,
    remaining_seconds_for_station,
)
from .bluetooth import async_get_connectable_device

from .models import IrrigationController, IrrigationStation
from .repairs import async_manage_bluetooth_issue

_LOGGER = logging.getLogger(__name__)


class SolemCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Poll BLE status and expose manual irrigation controls."""

    def __init__(self, hass: HomeAssistant, config_entry: MyConfigEntry) -> None:
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
            config_entry=config_entry,
            always_update=False,
        )

        self.num_stations = config_entry.data.get(NUM_STATIONS, 2)
        self.config_entry = config_entry

        self.controller = IrrigationController(
            device_id=f"{self.controller_mac_address}_irrigation_controller_status",
            device_name="Controller Status",
            device_uid="",
            software_version=None,
        )
        self.station_names: dict[int, str] = {}
        self.firmware_version: str | None = None
        self._firmware_retry_after = 0.0
        self._station_names_retry_after = 0.0
        self.stations = self._build_stations()

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
        self.battery_low: bool | None = None
        self._has_status = False
        self.irrigation_manual_duration = DEFAULT_MANUAL_DURATION
        self.remaining_seconds: int | None = None
        self.active_station_num: int | None = None
        self.active_program_num: int | None = None
        self.watering_origin: str | None = None
        self.irrigation_programs: dict[int, IrrigationProgram] = {}
        self._irrigation_config_retry_after = 0.0
        self._irrigation_config_refresh_after = 0.0
        self.schedule_coordinator = SolemScheduleCoordinator(hass, config_entry, self)
        self._last_set_time_at = 0.0
        self._last_set_time_sync: datetime | None = None
        self._set_time_pending = True
        self._consecutive_update_failures = 0

        _LOGGER.info(
            "%s - Coordinator initialization finished.",
            self.controller_mac_address,
        )

    def _station_name(self, station_id: int) -> str:
        """Return the controller-provided station name or a stable fallback."""
        return self.station_names.get(station_id) or f"Station {station_id}"

    def _program_display_name(self, program_index: int) -> str:
        """Return the on-device program name or a stable slot fallback."""
        program = self.irrigation_programs.get(program_index)
        if program and (name := program.get("name", "").strip()):
            return name
        return f"Program {PROGRAM_LABELS[program_index]}"

    def _build_stations(self) -> list[IrrigationStation]:
        """Build station models for the configured station count."""
        return [
            IrrigationStation(
                device_id=f"{self.controller_mac_address}_irrigation_station_{station_id}_status",
                device_name=f"{self._station_name(station_id)} Status",
                device_uid="",
                station_number=station_id,
                software_version=self.firmware_version,
            )
            for station_id in range(1, self.num_stations + 1)
        ]

    async def async_shutdown(self) -> None:
        """Release BLE resources when the integration unloads."""
        self.irrigation_stop_event.set()
        task = self._irrigation_monitor_task
        if task is not None and not task.done():
            task.cancel()
        await self._await_irrigation_monitor_task()
        self._clear_irrigation_idle_state()
        await self.schedule_coordinator.async_shutdown()
        await self.api.disconnect()

    def request_schedule_refresh(self) -> None:
        """Mark schedule data due for the next slow-coordinator refresh."""
        self._irrigation_config_refresh_after = 0.0

    async def async_init(self) -> None:
        """Build initial entity data without blocking setup on BLE availability."""
        self._ready = True
        self.data = await self.async_update_all_sensors(fetch_status=False)
        self.last_update_success = False

    def _apply_status(self, status: dict[str, Any]) -> None:
        """Update coordinator state from a BLE status dict."""
        apply_status(self, status)

    async def _fetch_device_status(self) -> dict[str, Any]:
        """Poll device and update controller/station states from BLE status."""
        return await fetch_device_status(self)

    async def _fetch_device_metadata(self) -> None:
        """Read firmware and station names without failing status polling."""
        await fetch_device_metadata(self)

    def _clear_irrigation_idle_state(self) -> None:
        """Reset coordinator state after irrigation stops or fails to start."""
        clear_irrigation_idle_state(self)

    def _clear_monitor_task_ref(self, task: asyncio.Task[None]) -> None:
        """Clear stored monitor task when it completes."""
        clear_monitor_task_ref(self, task)

    async def _await_irrigation_monitor_task(self) -> None:
        """Wait for the background irrigation monitor to finish."""
        await await_irrigation_monitor_task(self)

    def _remaining_seconds_for_station(self, station_id: int) -> int | None:
        """Return remaining sprinkle seconds for a station (0 when idle/inactive)."""
        return remaining_seconds_for_station(self, station_id)

    async def async_update_all_sensors(
        self, *, fetch_status: bool = True
    ) -> list[dict[str, Any]]:
        """Build entity descriptor list from current coordinator state."""
        if fetch_status and not self._irrigation_active:
            await self._fetch_device_status()
        return build_all_descriptors(self)

    async def async_update_data(self) -> list[dict[str, Any]]:
        try:
            data = await self.async_update_all_sensors()
            async_manage_bluetooth_issue(self, success=True)
            return data
        except Exception as err:
            async_manage_bluetooth_issue(self, success=False)
            raise UpdateFailed(f"Failed to update BLE status: {err}") from err

    async def start_irrigation(
        self, station: int, minutes: int | None = None
    ) -> None:
        """Send a start command, then monitor watering in the background."""
        await irrigation_start(self, station, minutes)

    async def _run_irrigation_monitor(self, station: int, duration: int) -> None:
        """Monitor active watering until completion, stop, or safety timeout."""
        await run_irrigation_monitor(self, station, duration)

    async def stop_irrigation(self) -> None:
        """Stop active manual watering."""
        await irrigation_stop(self)

    async def turn_controller_on(self) -> None:
        """Turn the irrigation controller on."""
        await irrigation_turn_on(self)

    async def turn_controller_off(self) -> None:
        """Turn the irrigation controller off permanently."""
        await irrigation_turn_off(self)

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


class SolemScheduleCoordinator(DataUpdateCoordinator[dict[int, IrrigationProgram]]):
    """Refresh persisted irrigation schedules without delaying status polls."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyConfigEntry,
        coordinator: SolemCoordinator,
    ) -> None:
        self.solem_coordinator = coordinator
        self._first_refresh_started = False
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} schedule ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=IRRIGATION_CONFIG_UPDATE_INTERVAL),
            config_entry=config_entry,
            always_update=False,
        )

    def async_start_first_refresh(self) -> None:
        """Start the first schedule refresh after schedule entities subscribe."""
        if self._first_refresh_started:
            return
        self._first_refresh_started = True
        self.hass.async_create_task(
            self.async_config_entry_first_refresh(),
            name=f"{DOMAIN} schedule first refresh",
        )

    async def async_update_data(self) -> dict[int, IrrigationProgram]:
        """Refresh schedule state and publish updated program descriptors."""
        await fetch_irrigation_config(self.solem_coordinator)
        self.solem_coordinator.async_set_updated_data(
            await self.solem_coordinator.async_update_all_sensors(fetch_status=False)
        )
        return self.solem_coordinator.irrigation_programs
