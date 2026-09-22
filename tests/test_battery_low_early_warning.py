"""Battery low binary sensor early-warning tests (issue #92)."""

from __future__ import annotations

import pytest

from custom_components.solem_blip.binary_sensor import BatteryLow
from custom_components.solem_blip.const import BATTERY_EARLY_WARNING_LEVEL
from custom_components.solem_blip.entity_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
)


@pytest.fixture
def battery_low_entity(coordinator) -> BatteryLow:
    """Return a BatteryLow entity wired to the shared coordinator fixture."""
    device = {"device_type": "BATTERY_LOW_SENSOR"}
    return BatteryLow(
        coordinator,
        device,
        "state",
        BINARY_SENSOR_DESCRIPTIONS["BATTERY_LOW_SENSOR"],
    )


@pytest.mark.asyncio
async def test_battery_low_on_protocol_alert(battery_low_entity, coordinator) -> None:
    """Protocol alert still triggers the sensor regardless of level."""
    coordinator.battery_low = True
    coordinator.battery_level = 5
    assert battery_low_entity.is_on is True


@pytest.mark.asyncio
async def test_battery_low_on_level_at_or_below_threshold(
    battery_low_entity, coordinator
) -> None:
    """Level 1 (or 0) triggers the sensor even when the protocol alert is false."""
    coordinator.battery_low = False
    coordinator.battery_level = BATTERY_EARLY_WARNING_LEVEL
    assert battery_low_entity.is_on is True

    coordinator.battery_level = 0
    assert battery_low_entity.is_on is True


@pytest.mark.asyncio
async def test_battery_low_off_when_healthy(battery_low_entity, coordinator) -> None:
    """No alert and level above the threshold stays off."""
    coordinator.battery_low = False
    coordinator.battery_level = 2
    assert battery_low_entity.is_on is False

    coordinator.battery_level = 5
    assert battery_low_entity.is_on is False


@pytest.mark.asyncio
async def test_battery_low_unknown_without_data(battery_low_entity, coordinator) -> None:
    """Unknown until either the protocol alert or a level has been seen."""
    coordinator.battery_low = None
    coordinator.battery_level = None
    assert battery_low_entity.is_on is None

    coordinator.battery_low = False
    coordinator.battery_level = None
    assert battery_low_entity.is_on is False

    coordinator.battery_low = None
    coordinator.battery_level = 3
    assert battery_low_entity.is_on is False
