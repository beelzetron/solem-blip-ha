"""Program running binary sensor tests."""

from __future__ import annotations

import pytest

from custom_components.solem_blip.binary_sensor import ProgramRunning
from custom_components.solem_blip.entity_descriptions import BINARY_SENSOR_DESCRIPTIONS


@pytest.mark.asyncio
async def test_program_running_binary_reflects_active_program(coordinator) -> None:
    coordinator.active_program_num = 3
    coordinator.data = await coordinator.async_update_all_sensors(fetch_status=False)

    device = next(
        item
        for item in coordinator.data
        if item["device_type"] == "PROGRAM_RUNNING_SENSOR"
        and item.get("program_num") == 3
    )
    entity = ProgramRunning(
        coordinator,
        device,
        "state",
        BINARY_SENSOR_DESCRIPTIONS["PROGRAM_RUNNING_SENSOR"],
    )
    assert entity.is_on is True

    coordinator.active_program_num = 1
    assert entity.is_on is False


@pytest.mark.asyncio
async def test_program_running_binary_ignores_invalid_program_num(
    coordinator,
) -> None:
    coordinator.data = await coordinator.async_update_all_sensors(fetch_status=False)
    device = next(
        item
        for item in coordinator.data
        if item["device_type"] == "PROGRAM_RUNNING_SENSOR"
    )
    device["program_num"] = "bad"
    entity = ProgramRunning(
        coordinator,
        device,
        "state",
        BINARY_SENSOR_DESCRIPTIONS["PROGRAM_RUNNING_SENSOR"],
    )
    assert entity.is_on is False
