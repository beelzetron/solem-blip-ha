"""Manual irrigation control helpers for SolemCoordinator."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import sleep
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError

from .api import APIConnectionError
from .const import DOMAIN
from .coordinator_polling import apply_status

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)


def clear_irrigation_idle_state(coordinator: SolemCoordinator) -> None:
    """Reset coordinator state after irrigation stops or fails to start."""
    coordinator._irrigation_active = False
    coordinator.active_station_num = None
    coordinator.remaining_seconds = None
    for station in coordinator.stations:
        station.state = "Stopped"


def clear_monitor_task_ref(
    coordinator: SolemCoordinator, task: asyncio.Task[None]
) -> None:
    """Clear stored monitor task when it completes."""
    if coordinator._irrigation_monitor_task is task:
        coordinator._irrigation_monitor_task = None


async def await_irrigation_monitor_task(coordinator: SolemCoordinator) -> None:
    """Wait for the background irrigation monitor to finish."""
    task = coordinator._irrigation_monitor_task
    if task is None:
        return
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def monitor_irrigation_until_complete(
    coordinator: SolemCoordinator, station: int, duration: int
) -> None:
    """Poll BLE status until watering stops or safety timeout."""
    poll_interval = max(2, min(coordinator.poll_interval, 10))
    max_seconds = duration * 60 + 30
    elapsed = 0.0

    try:
        while elapsed < max_seconds:
            if coordinator.irrigation_stop_event.is_set():
                _LOGGER.info(
                    "%s - Irrigation cancellation triggered.",
                    coordinator.controller_mac_address,
                )
                break

            await sleep(poll_interval)
            elapsed += poll_interval

            try:
                status = await coordinator.api.get_status()
            except APIConnectionError as ex:
                _LOGGER.warning(
                    "%s - Status poll failed during irrigation: %s",
                    coordinator.controller_mac_address,
                    ex,
                )
                continue

            apply_status(coordinator, status)
            if not status.get("is_watering"):
                _LOGGER.info(
                    "%s - Device reports watering finished.",
                    coordinator.controller_mac_address,
                )
                break

            coordinator.async_set_updated_data(
                await coordinator.async_update_all_sensors()
            )
        else:
            _LOGGER.warning(
                "%s - Irrigation monitor hit safety timeout after %ss",
                coordinator.controller_mac_address,
                max_seconds,
            )
    finally:
        clear_irrigation_idle_state(coordinator)
        _LOGGER.info(
            "%s - Finished watering on station %s.",
            coordinator.controller_mac_address,
            station,
        )


async def run_irrigation_monitor(
    coordinator: SolemCoordinator, station: int, duration: int
) -> None:
    """Monitor active watering until completion, stop, or safety timeout."""
    try:
        await monitor_irrigation_until_complete(coordinator, station, duration)
        data = await coordinator.async_update_all_sensors()
        coordinator.async_set_updated_data(data)
    except asyncio.CancelledError:
        clear_irrigation_idle_state(coordinator)
        raise
    except Exception as err:
        _LOGGER.error(
            "%s - Irrigation monitor failed for station %s: %s",
            coordinator.controller_mac_address,
            station,
            err,
            exc_info=True,
        )


async def start_irrigation(
    coordinator: SolemCoordinator, station: int, minutes: int | None = None
) -> None:
    """Send a start command, then monitor watering in the background."""
    if coordinator._irrigation_active:
        raise APIConnectionError("Irrigation is already in progress")

    duration = int(
        minutes if minutes is not None else coordinator.irrigation_manual_duration
    )
    _LOGGER.info(
        "%s - Starting watering on station %s for %s minutes...",
        coordinator.controller_mac_address,
        station,
        duration,
    )

    coordinator.irrigation_stop_event.clear()
    coordinator._irrigation_active = True
    coordinator.stations[station - 1].state = "Sprinkling"
    coordinator.async_set_updated_data(await coordinator.async_update_all_sensors())

    coordinator._irrigation_monitor_task = coordinator.hass.async_create_task(
        coordinator._run_irrigation_monitor(station, duration),
        name=f"{DOMAIN} irrigation station {station}",
    )
    coordinator._irrigation_monitor_task.add_done_callback(
        lambda task: clear_monitor_task_ref(coordinator, task)
    )

    try:
        await coordinator.api.sprinkle_station_x_for_y_minutes(station, duration)
    except APIConnectionError as ex:
        _LOGGER.error(
            "%s - Failed to start irrigation due to connection error.",
            coordinator.controller_mac_address,
            exc_info=True,
        )
        task = coordinator._irrigation_monitor_task
        if task is not None and not task.done():
            task.cancel()
        await await_irrigation_monitor_task(coordinator)
        clear_irrigation_idle_state(coordinator)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="start_irrigation_failed",
            translation_placeholders={"station": str(station)},
        ) from ex
    except Exception as ex:
        _LOGGER.error(
            "%s - Failed to start irrigation due to error: %s",
            coordinator.controller_mac_address,
            ex,
            exc_info=True,
        )
        task = coordinator._irrigation_monitor_task
        if task is not None and not task.done():
            task.cancel()
        await await_irrigation_monitor_task(coordinator)
        clear_irrigation_idle_state(coordinator)
        raise


async def stop_irrigation(coordinator: SolemCoordinator) -> None:
    """Stop active manual watering."""
    _LOGGER.info("%s - Stopping watering...", coordinator.controller_mac_address)
    try:
        await coordinator.api.stop_manual_sprinkle()
    except APIConnectionError as ex:
        _LOGGER.error(
            "%s - Failed to stop irrigation due to connection error.",
            coordinator.controller_mac_address,
        )
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="stop_irrigation_failed",
        ) from ex
    await await_irrigation_monitor_task(coordinator)
    clear_irrigation_idle_state(coordinator)

    _LOGGER.info("%s - Stopped watering.", coordinator.controller_mac_address)
    coordinator.async_set_updated_data(await coordinator.async_update_all_sensors())


async def turn_controller_on(coordinator: SolemCoordinator) -> None:
    """Turn the irrigation controller on."""
    _LOGGER.info(
        "%s - Turning irrigation controller on...",
        coordinator.controller_mac_address,
    )
    try:
        await coordinator.api.turn_on()
    except APIConnectionError as ex:
        _LOGGER.error(
            "%s - Failed to turn controller on due to connection error.",
            coordinator.controller_mac_address,
        )
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="controller_on_failed",
        ) from ex
    coordinator.async_set_updated_data(await coordinator.async_update_all_sensors())
    _LOGGER.info(
        "%s - Irrigation controller turned on.",
        coordinator.controller_mac_address,
    )


async def turn_controller_off(coordinator: SolemCoordinator) -> None:
    """Turn the irrigation controller off permanently."""
    _LOGGER.info(
        "%s - Turning irrigation controller off...",
        coordinator.controller_mac_address,
    )
    try:
        await coordinator.api.turn_off_permanent()
    except APIConnectionError as ex:
        _LOGGER.error(
            "%s - Failed to turn controller off due to connection error.",
            coordinator.controller_mac_address,
        )
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="controller_off_failed",
        ) from ex
    coordinator.async_set_updated_data(await coordinator.async_update_all_sensors())
    _LOGGER.info(
        "%s - Irrigation controller turned off.",
        coordinator.controller_mac_address,
    )
