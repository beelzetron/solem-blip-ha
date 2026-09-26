"""Dedicated watering-activity diagnostic sensor tests (#103)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import RuntimeData
from custom_components.solem_blip.const import (
    CONTROLLER_MAC_ADDRESS,
    DOMAIN,
    NUM_STATIONS,
)
from custom_components.solem_blip.activity import HISTORY_LIMIT
from custom_components.solem_blip.entity_descriptions import SENSOR_DESCRIPTIONS
from custom_components.solem_blip.entity_identity import iter_entity_identities
from custom_components.solem_blip.sensor import (
    StateSensor,
    WateringActivitySensor,
    async_setup_entry as setup_sensor,
)
from tests.conftest import MOCK_IRRIGATION_PROGRAMS

IDLE = {"is_watering": False, "active_program": None, "station_num": None}
RUN = {"is_watering": True, "active_program": 1, "station_num": 1}


@pytest.fixture
def activity(coordinator, freezer):
    """Configure the coordinator like the tracker tests do."""
    freezer.move_to("2026-09-18T12:00:00+00:00")
    coordinator.irrigation_programs = {
        index: dict(program)
        for index, program in MOCK_IRRIGATION_PROGRAMS.items()
    }
    coordinator.irrigation_programs[0].update(
        cycle=0, week_days=127, start_times=[720] + [None] * 7
    )
    coordinator.program_backup.last_read = dt_util.utcnow().isoformat()
    return coordinator.activity


@pytest.fixture
def activity_sensor(coordinator) -> WateringActivitySensor:
    """Build the sensor entity against the coordinator fixture."""
    device = next(
        item
        for item in coordinator.data
        if item["device_type"] == "WATERING_ACTIVITY_SENSOR"
    )
    return WateringActivitySensor(
        coordinator,
        device,
        "state",
        SENSOR_DESCRIPTIONS["WATERING_ACTIVITY_SENSOR"],
    )


async def test_descriptor_is_diagnostic_enum_sensor() -> None:
    """The description carries diagnostic category and the full state set."""
    description = SENSOR_DESCRIPTIONS["WATERING_ACTIVITY_SENSOR"]
    assert description.entity_category is not None
    assert description.entity_category.value == "diagnostic"
    assert description.device_class is not None
    assert description.device_class.value == "enum"
    assert list(description.options) == [
        "idle",
        "Manual Home Assistant",
        "Scheduled",
        "Manual Bluetooth",
        "Unknown",
    ]


async def test_identity_slot_is_appended_last() -> None:
    """The new entity keeps legacy counter slots stable (slot 1402 at the end)."""
    identities = list(iter_entity_identities("AA:BB:CC:DD:EE:FF", 2))
    watering = identities[-1]
    assert watering.device_type == "WATERING_ACTIVITY_SENSOR"
    assert watering.device_uid == "AABB-CCDD-EEFF-1402"
    assert watering.platform == "sensor"
    # The time-alarm slot that previously closed the list is untouched.
    assert identities[-2].device_type == "TIME_ALARM_SENSOR"
    assert identities[-2].device_uid == "AABB-CCDD-EEFF-1401"


async def test_idle_state_and_attributes(activity, activity_sensor) -> None:
    """With no run in progress the sensor reads idle with empty attributes."""
    activity.observe(IDLE)
    assert activity_sensor.native_value == "idle"
    assert activity_sensor.extra_state_attributes == {
        "current": None,
        "history": [],
    }


async def test_manual_ha_run_state_and_transition(activity, activity_sensor) -> None:
    """A manual HA run surfaces the source, then idle again after it ends."""
    activity.observe(IDLE)
    async with activity.command(program=1):
        pass
    activity.observe(RUN)
    assert activity_sensor.native_value == "Manual Home Assistant"
    attributes = activity_sensor.extra_state_attributes
    assert attributes["current"]["source"] == "Manual Home Assistant"
    assert attributes["history"] == []
    activity.observe(IDLE)
    assert activity_sensor.native_value == "idle"
    attributes = activity_sensor.extra_state_attributes
    assert attributes["current"] is None
    assert attributes["history"][0]["outcome"] == "Finished"
    assert attributes["history"][0]["source"] == "Manual Home Assistant"


async def test_scheduled_run_state(activity, activity_sensor, freezer) -> None:
    """A scheduled run surfaces the Scheduled source while it runs."""
    freezer.move_to("2026-09-18T11:59:00+00:00")
    activity.c.program_backup.last_read = dt_util.utcnow().isoformat()
    at_start = dt_util.now() + timedelta(minutes=1)
    activity.c.irrigation_programs[0]["start_times"] = [
        at_start.hour * 60 + at_start.minute
    ] + [None] * 7
    activity.observe(IDLE)
    freezer.tick(timedelta(seconds=60))
    activity.observe(RUN)
    assert activity_sensor.native_value == "Scheduled"
    assert activity_sensor.extra_state_attributes["current"]["program"] == 1


async def test_history_attribute_is_newest_first_and_capped(
    activity, activity_sensor
) -> None:
    """Attributes mirror the tracker history exactly: newest first, 30 max."""
    activity.observe(RUN)
    for _ in range(HISTORY_LIMIT + 5):
        activity.observe(IDLE)
        activity.observe(RUN)
    attributes = activity_sensor.extra_state_attributes
    history = attributes["history"]
    assert len(history) == HISTORY_LIMIT
    finished = [
        record["finished_detected"]
        for record in history
        if record["finished_detected"] is not None
    ]
    assert finished == sorted(finished, reverse=True)
    assert attributes["current"]["outcome"] == "Running"
    # The attribute payload is a copy: mutating it cannot corrupt the tracker.
    attributes["history"].clear()
    assert len(activity.history) == HISTORY_LIMIT


async def test_restart_persistence_preserves_state(coordinator, hass) -> None:
    """A reloaded coordinator restores history and current from the store."""
    coordinator.activity.observe(IDLE)
    async with coordinator.activity.command(program=1):
        pass
    coordinator.activity.observe(RUN)
    coordinator.activity.observe(IDLE)
    stored = coordinator.activity._stored()
    assert stored["history"]

    replacement = coordinator.__class__(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data={
                CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
                NUM_STATIONS: 2,
            },
            unique_id="AA:BB:CC:DD:EE:FF",
        ),
    )
    replacement.activity.store.async_load = AsyncMock(return_value=stored)
    await replacement.activity.load()

    device = {
        "device_id": f"{replacement.controller_mac_address}_watering_activity",
        "device_type": "WATERING_ACTIVITY_SENSOR",
    }
    sensor = WateringActivitySensor(
        replacement, device, "state", SENSOR_DESCRIPTIONS["WATERING_ACTIVITY_SENSOR"]
    )
    # The restored run is finished (a restart cannot observe its end), and the
    # history survived: the sensor reads idle with the previous run recorded.
    assert sensor.native_value == "idle"
    attributes = sensor.extra_state_attributes
    assert attributes["current"] is None
    assert attributes["history"][0]["source"] == "Manual Home Assistant"


async def test_status_sensor_watering_activity_attribute_unchanged(
    coordinator, freezer
) -> None:
    """The controller-status sensor keeps exposing the full tracker state."""
    freezer.move_to("2026-09-18T12:00:00+00:00")
    device = next(
        item
        for item in coordinator.data
        if item["device_id"].endswith("_irrigation_controller_status")
    )
    entity = StateSensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["STATE_SENSOR"]
    )
    coordinator.activity.observe(RUN)
    attributes = entity.extra_state_attributes
    assert attributes["watering_activity"] == {
        "current": coordinator.activity.current,
        "history": [],
    }


async def test_platform_setup_creates_one_sensor_per_controller(hass, coordinator, mock_config_entry) -> None:
    """The sensor platform registers exactly one watering-activity entity."""
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    entities: list = []
    await setup_sensor(hass, mock_config_entry, entities.extend)
    watering = [
        entity
        for entity in entities
        if isinstance(entity, WateringActivitySensor)
    ]
    assert len(watering) == 1
    assert watering[0].native_value == "idle"
