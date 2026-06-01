"""Direct sensor property coverage."""

from __future__ import annotations

import pytest

from custom_components.solem_blip.entity_descriptions import SENSOR_DESCRIPTIONS
from custom_components.solem_blip.sensor import (
    BatteryVoltageSensor,
    LastTimeSyncSensor,
    ProgramNameSensor,
    ProgramScheduleSensor,
    RemainingSprinkleSensor,
    StateSensor,
)
from tests.conftest import MOCK_IRRIGATION_PROGRAMS


def _seed_coordinator_state(coordinator) -> None:
    """Populate coordinator fields used by descriptor builders in tests."""
    coordinator.battery_level = 5
    coordinator.battery_voltage = 90
    coordinator.controller.state = "On"
    coordinator.stations[0].state = "Stopped"
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
