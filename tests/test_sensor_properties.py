"""Direct sensor property coverage."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import UnitOfTime

from custom_components.solem_blip.entity_descriptions import SENSOR_DESCRIPTIONS
from custom_components.solem_blip.sensor import (
    BatteryVoltageSensor,
    LastTimeSyncSensor,
    ProgramNextStartSensor,
    ProgramScheduleSensor,
    RemainingSprinkleSensor,
    StateSensor,
)
from tests.conftest import MOCK_IRRIGATION_PROGRAMS


def _seed_coordinator_state(coordinator) -> None:
    """Populate coordinator fields used by descriptor builders in tests."""
    coordinator.battery_level = 5
    coordinator.battery_voltage = 90
    coordinator.controller.state = "on"
    coordinator.stations[0].state = "stopped"
    coordinator._has_status = True
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)


@pytest.mark.asyncio
async def test_sensor_native_values_after_refresh(coordinator) -> None:
    """Each sensor subclass reads values from coordinator descriptors."""
    _seed_coordinator_state(coordinator)
    coordinator.data = await coordinator.async_update_all_sensors(fetch_status=False)

    samples = {
        "STATE_SENSOR": StateSensor,
        "BATTERY_VOLTAGE_SENSOR": BatteryVoltageSensor,
        "REMAINING_SPRINKLE_SENSOR": RemainingSprinkleSensor,
        "LAST_TIME_SYNC_SENSOR": LastTimeSyncSensor,
        "PROGRAM_NEXT_START_SENSOR": ProgramNextStartSensor,
        "PROGRAM_SCHEDULE_SENSOR": ProgramScheduleSensor,
    }

    for device_type, entity_class in samples.items():
        device = next(item for item in coordinator.data if item["device_type"] == device_type)
        entity = entity_class(
            coordinator, device, "state", SENSOR_DESCRIPTIONS[device_type]
        )
        assert entity.native_value is not None or device_type in {
            "LAST_TIME_SYNC_SENSOR",
            "PROGRAM_NEXT_START_SENSOR",
        }


def test_remaining_time_sensor_uses_minutes() -> None:
    """Station remaining-time sensors expose minutes to Home Assistant."""
    description = SENSOR_DESCRIPTIONS["REMAINING_SPRINKLE_SENSOR"]
    assert description.device_class is None
    assert description.native_unit_of_measurement == UnitOfTime.MINUTES
    assert description.suggested_unit_of_measurement == UnitOfTime.MINUTES


@pytest.mark.asyncio
async def test_controller_status_sensor_exposes_program_attributes(
    coordinator,
) -> None:
    """Controller status sensor surfaces active program fields while watering."""
    _seed_coordinator_state(coordinator)
    coordinator.active_program_num = 3
    coordinator.watering_origin = "program"
    coordinator._is_watering = True
    coordinator.active_station_num = 5
    coordinator.remaining_seconds = 900
    coordinator.data = await coordinator.async_update_all_sensors(fetch_status=False)

    device = next(
        item
        for item in coordinator.data
        if item["device_id"].endswith("_irrigation_controller_status")
    )
    entity = StateSensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["STATE_SENSOR"]
    )
    assert entity.extra_state_attributes == {
        "is_watering": True,
        "active_station": 5,
        "remaining_seconds": 900,
        "active_program": 3,
        "active_program_name": "Programma C",
        "watering_origin": "program",
    }


@pytest.mark.asyncio
async def test_controller_status_sensor_shows_manual_watering_on_start(
    hass,
    mock_config_entry,
    mock_solem_client,
) -> None:
    """Manual irrigation start exposes controller watering attributes immediately."""
    from unittest.mock import patch

    from custom_components.solem_blip.coordinator import SolemCoordinator
    from custom_components.solem_blip.entity_descriptions import SENSOR_DESCRIPTIONS
    from custom_components.solem_blip.sensor import StateSensor

    async def block_sprinkle(*_args, **_kwargs) -> None:
        await release_sprinkle.wait()

    release_sprinkle = asyncio.Event()
    mock_solem_client.sprinkle_station_x_for_y_minutes = block_sprinkle

    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()
        start_task = asyncio.create_task(coordinator.start_irrigation(station=1, minutes=1))
        await asyncio.sleep(0)

        device = next(
            item
            for item in coordinator.data
            if item["device_id"].endswith("_irrigation_controller_status")
        )
        entity = StateSensor(
            coordinator, device, "state", SENSOR_DESCRIPTIONS["STATE_SENSOR"]
        )
        assert entity.extra_state_attributes["is_watering"] is True
        assert entity.extra_state_attributes["active_station"] == 1

        coordinator.irrigation_stop_event.set()
        release_sprinkle.set()
        task = coordinator._irrigation_monitor_task
        if task is not None and not task.done():
            task.cancel()
        await start_task


@pytest.mark.asyncio
async def test_station_status_sensor_has_no_program_attributes(coordinator) -> None:
    """Station status sensors do not expose controller program attributes."""
    _seed_coordinator_state(coordinator)
    coordinator.data = await coordinator.async_update_all_sensors(fetch_status=False)
    device = next(
        item
        for item in coordinator.data
        if item["device_id"].endswith("_irrigation_station_1_status")
    )
    entity = StateSensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["STATE_SENSOR"]
    )
    assert entity.extra_state_attributes == {}


@pytest.mark.asyncio
async def test_program_next_start_sensor_exposes_schedule_context(coordinator) -> None:
    """Program next-start sensor forwards schedule context attributes."""
    _seed_coordinator_state(coordinator)
    await coordinator.schedule_coordinator.async_refresh()
    coordinator.data = await coordinator.async_update_all_sensors(fetch_status=False)
    device = next(
        item
        for item in coordinator.data
        if item["device_type"] == "PROGRAM_NEXT_START_SENSOR"
        and item.get("translation_placeholders", {}).get("program_name") == "Programma C"
    )
    entity = ProgramNextStartSensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["PROGRAM_NEXT_START_SENSOR"]
    )
    attrs = entity.extra_state_attributes
    assert attrs["cycle"] == 4
    assert attrs["period_start_date"] == "2026-06-01"
    assert attrs["minutes_since_midnight"] == 270
