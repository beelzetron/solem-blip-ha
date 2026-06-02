"""BLE polling helpers for SolemCoordinator."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    HEAVY_READ_DEFER_SECONDS,
    IRRIGATION_CONFIG_READ_TIMEOUT,
    IRRIGATION_CONFIG_REFRESH_INTERVAL,
    IRRIGATION_CONFIG_RETRY_INTERVAL,
    METADATA_READ_TIMEOUT,
    METADATA_RETRY_INTERVAL,
    PROGRAM_LABELS,
    SET_TIME_MIN_INTERVAL,
    STATION_NAMES_READ_TIMEOUT,
)
from .util import normalize_entity_state
from .coordinator_publish import publish_descriptor_update

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)


def active_program_name(coordinator: SolemCoordinator) -> str | None:
    """Return the on-device program name for the active program index, if known."""
    program_num = coordinator.active_program_num
    if program_num is None or not 1 <= program_num <= len(PROGRAM_LABELS):
        return None
    return coordinator._program_display_name(program_num - 1)


def apply_status(coordinator: SolemCoordinator, status: dict[str, Any]) -> None:
    """Update coordinator state from a BLE status dict."""
    coordinator.controller.state = normalize_entity_state(
        status.get("controller_state")
    )
    coordinator.battery_voltage = status.get("battery_voltage")
    coordinator.battery_level = status.get("battery_level")
    coordinator.battery_low = bool(status.get("battery_low", False))
    coordinator.controller_off_mode = status.get("controller_off_mode", "unknown")
    coordinator.controller_off_days_remaining = status.get(
        "controller_off_days_remaining"
    )
    coordinator._has_status = True
    coordinator._is_watering = bool(status.get("is_watering"))
    active_program = status.get("active_program")
    if active_program is not None:
        coordinator.active_program_num = active_program
    elif not status.get("is_watering"):
        coordinator.active_program_num = None
    watering_origin = status.get("watering_origin")
    if coordinator.active_program_num is not None:
        watering_origin = "program"
    elif status.get("is_watering") and status.get("active_program"):
        watering_origin = "program"
    coordinator.watering_origin = watering_origin

    if status.get("is_watering") and status.get("station_num"):
        active_station_num = status["station_num"]
        coordinator.active_station_num = active_station_num
        coordinator.remaining_seconds = status.get("remaining_seconds")
        if 1 <= active_station_num <= coordinator.num_stations:
            for index, station in enumerate(coordinator.stations):
                station.state = (
                    "sprinkling"
                    if index + 1 == active_station_num
                    else "stopped"
                )
        else:
            _LOGGER.warning(
                "%s - Watering on station %s outside configured range 1-%s",
                coordinator.controller_mac_address,
                active_station_num,
                coordinator.num_stations,
            )
    elif not status.get("is_watering"):
        coordinator.active_station_num = None
        coordinator.remaining_seconds = None
        if coordinator.active_program_num is None:
            coordinator.watering_origin = None
        for station in coordinator.stations:
            station.state = "stopped"
    else:
        _LOGGER.warning(
            "%s - Watering active but no station in status; keeping existing states",
            coordinator.controller_mac_address,
        )

    _LOGGER.debug(
        (
            "%s - Status: controller=%s watering=%s station=%s program=%s "
            "origin=%s remaining=%ss battery=%s (%s/5)"
        ),
        coordinator.controller_mac_address,
        coordinator.controller.state,
        status.get("is_watering"),
        status.get("station_num"),
        coordinator.active_program_num,
        coordinator.watering_origin,
        coordinator.remaining_seconds,
        coordinator.battery_voltage,
        coordinator.battery_level,
    )


async def maybe_set_device_time(coordinator: SolemCoordinator) -> None:
    """Push HA local time to the device when throttling allows."""
    if coordinator.solem_api_mock or coordinator.api.mock:
        return
    if coordinator._irrigation_active:
        return

    now = asyncio.get_running_loop().time()
    if (
        not coordinator._set_time_pending
        and coordinator._last_set_time_at
        and now - coordinator._last_set_time_at < SET_TIME_MIN_INTERVAL
    ):
        return

    moment = datetime.now().astimezone()
    try:
        await coordinator.api.set_time(moment)
    except Exception as err:
        _LOGGER.warning(
            "%s - Failed to sync device time: %s",
            coordinator.controller_mac_address,
            str(err) or type(err).__name__,
        )
        return

    coordinator._set_time_pending = False
    coordinator._last_set_time_at = now
    coordinator._last_set_time_sync = moment
    _LOGGER.debug(
        "%s - Device time synced to %s",
        coordinator.controller_mac_address,
        moment.isoformat(),
    )


async def fetch_device_metadata(coordinator: SolemCoordinator) -> None:
    """Read firmware and station names without failing status polling."""
    async with coordinator._heavy_read_lock:
        await _fetch_device_metadata_locked(coordinator)


async def _fetch_device_metadata_locked(coordinator: SolemCoordinator) -> None:
    """Read firmware and station names while holding the heavy-read lock."""
    now = asyncio.get_running_loop().time()
    if coordinator.firmware_version is None and now >= coordinator._firmware_retry_after:
        try:
            firmware = await asyncio.wait_for(
                coordinator.api.get_firmware_version(),
                timeout=METADATA_READ_TIMEOUT,
            )
        except Exception as err:
            coordinator._firmware_retry_after = now + METADATA_RETRY_INTERVAL
            _LOGGER.warning(
                "%s - Failed to read firmware version: %s",
                coordinator.controller_mac_address,
                str(err) or type(err).__name__,
            )
        else:
            coordinator.firmware_version = firmware["raw_hex"]
            coordinator.controller.software_version = coordinator.firmware_version
            for station in coordinator.stations:
                station.software_version = coordinator.firmware_version
            device_registry = dr.async_get(coordinator.hass)
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, coordinator.controller_mac_address)}
            )
            if device is not None:
                device_registry.async_update_device(
                    device.id, sw_version=coordinator.firmware_version
                )

    now = asyncio.get_running_loop().time()
    if (
        len(coordinator.station_names) < coordinator.num_stations
        and now >= coordinator._station_names_retry_after
    ):
        try:
            station_names = await asyncio.wait_for(
                coordinator.api.get_station_names(),
                timeout=STATION_NAMES_READ_TIMEOUT,
            )
        except Exception as err:
            coordinator._station_names_retry_after = now + METADATA_RETRY_INTERVAL
            _LOGGER.warning(
                "%s - Failed to read station names: %s",
                coordinator.controller_mac_address,
                str(err) or type(err).__name__,
            )
        else:
            coordinator.station_names.update(
                {
                    station_id: name.strip() or f"Station {station_id}"
                    for station_id, name in station_names.items()
                    if 1 <= station_id <= coordinator.num_stations
                }
            )
            for station in coordinator.stations:
                station.device_name = (
                    f"{coordinator._station_name(station.station_number)} Status"
                )
            publish_descriptor_update(
                coordinator,
                await coordinator.async_update_all_sensors(fetch_status=False),
            )


def _mark_first_successful_status_poll(coordinator: SolemCoordinator) -> None:
    """Record the first successful status poll and defer heavy BLE reads."""
    now = asyncio.get_running_loop().time()
    if coordinator._first_successful_status_at is not None:
        return
    coordinator._first_successful_status_at = now
    coordinator._metadata_ready_after = now + HEAVY_READ_DEFER_SECONDS
    coordinator._schedule_ready_after = now + HEAVY_READ_DEFER_SECONDS
    _LOGGER.debug(
        "%s - First status poll succeeded; deferring metadata/schedule reads for %ss",
        coordinator.controller_mac_address,
        HEAVY_READ_DEFER_SECONDS,
    )


def _heavy_reads_ready(coordinator: SolemCoordinator) -> bool:
    """Return True when deferred metadata/schedule reads may start."""
    if coordinator._first_successful_status_at is None:
        return False
    return asyncio.get_running_loop().time() >= coordinator._metadata_ready_after


async def fetch_irrigation_config(coordinator: SolemCoordinator) -> None:
    """Read on-device irrigation programs without failing status polling."""
    async with coordinator._heavy_read_lock:
        await _fetch_irrigation_config_locked(coordinator)


async def _fetch_irrigation_config_locked(coordinator: SolemCoordinator) -> None:
    """Read irrigation programs while holding the heavy-read lock."""
    if coordinator._irrigation_active:
        return

    now = asyncio.get_running_loop().time()
    has_programs = bool(coordinator.irrigation_programs)
    if has_programs and now < coordinator._irrigation_config_refresh_after:
        return
    if not has_programs and now < coordinator._irrigation_config_retry_after:
        return

    try:
        programs = await asyncio.wait_for(
            coordinator.api.get_irrigation_config(),
            timeout=IRRIGATION_CONFIG_READ_TIMEOUT,
        )
    except Exception as err:
        coordinator._irrigation_config_retry_after = (
            now + IRRIGATION_CONFIG_RETRY_INTERVAL
        )
        _LOGGER.warning(
            "%s - Failed to read irrigation config: %s",
            coordinator.controller_mac_address,
            str(err) or type(err).__name__,
        )
        return

    coordinator.irrigation_programs = {
        index: programs[index] for index in (0, 1, 2) if index in programs
    }
    coordinator._irrigation_config_refresh_after = (
        now + IRRIGATION_CONFIG_REFRESH_INTERVAL
    )
    coordinator._irrigation_config_retry_after = 0.0


async def fetch_device_status(coordinator: SolemCoordinator) -> dict[str, Any]:
    """Poll device and update controller/station states from BLE status."""
    if not coordinator._irrigation_active:
        await maybe_set_device_time(coordinator)
    status = await coordinator.api.get_status()
    apply_status(coordinator, status)
    _mark_first_successful_status_poll(coordinator)
    if not coordinator._irrigation_active and _heavy_reads_ready(coordinator):
        config_entry = coordinator.config_entry
        metadata_task = coordinator._metadata_task
        if (
            config_entry is not None
            and (metadata_task is None or metadata_task.done())
        ):
            coordinator._metadata_task = config_entry.async_create_background_task(
                coordinator.hass,
                fetch_device_metadata(coordinator),
                f"{DOMAIN} metadata refresh ({coordinator.controller_mac_address})",
            )
    elif (
        not coordinator._irrigation_active
        and coordinator._first_successful_status_at is not None
        and not _heavy_reads_ready(coordinator)
    ):
        _LOGGER.debug(
            "%s - Metadata read deferred until heavy-read gate elapses",
            coordinator.controller_mac_address,
        )
    return status


def remaining_minutes_for_station(
    coordinator: SolemCoordinator, station_id: int
) -> int | None:
    """Return remaining sprinkle minutes for a station (0 when idle/inactive)."""
    if not coordinator._has_status:
        return None
    if (
        coordinator.active_station_num == station_id
        and coordinator.remaining_seconds is not None
    ):
        return math.ceil(coordinator.remaining_seconds / 60)
    if (
        coordinator.active_station_num == station_id
        and coordinator.remaining_seconds is None
    ):
        return None
    return 0
