"""Direct sensor property coverage."""

from __future__ import annotations

import pytest

from custom_components.solem_blip.entity_descriptions import SENSOR_DESCRIPTIONS
from custom_components.solem_blip.sensor import (
    BatteryVoltageSensor,
    LastTimeSyncSensor,
    ProgramNameSensor,
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
        "PROGRAM_NAME_SENSOR": ProgramNameSensor,
        "PROGRAM_SCHEDULE_SENSOR": ProgramScheduleSensor,
    }

    for device_type, entity_class in samples.items():
        device = next(item for item in coordinator.data if item["device_type"] == device_type)
        entity = entity_class(
            coordinator, device, "state", SENSOR_DESCRIPTIONS[device_type]
        )
        assert entity.native_value is not None or device_type in {
            "LAST_TIME_SYNC_SENSOR",
        }


@pytest.mark.asyncio
async def test_controller_status_sensor_exposes_program_attributes(
    coordinator,
) -> None:
    """Controller status sensor surfaces active program fields while watering."""
    _seed_coordinator_state(coordinator)
    coordinator.active_program_num = 3
    coordinator.watering_origin = "program"
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
        "active_program": 3,
        "active_program_name": "Programma C",
        "watering_origin": "program",
    }


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
        and item.get("translation_placeholders", {}).get("program") == "C"
    )
    entity = ProgramNextStartSensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["PROGRAM_NEXT_START_SENSOR"]
    )
    attrs = entity.extra_state_attributes
    assert attrs["cycle"] == 4
    assert attrs["period_start_date"] == "2026-06-01"
    assert attrs["minutes_since_midnight"] == 270
