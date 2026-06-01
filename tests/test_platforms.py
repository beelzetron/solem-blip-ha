"""Platform entity setup and interaction tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import RuntimeData
from custom_components.solem_blip.api import APIConnectionError
from custom_components.solem_blip.binary_sensor import async_setup_entry as setup_binary
from custom_components.solem_blip.button import (
    ControllerOffButton,
    ControllerOnButton,
    IrrigationStartButton,
    IrrigationStopButton,
    async_setup_entry as setup_button,
)
from custom_components.solem_blip.entity_descriptions import (
    BUTTON_DESCRIPTIONS,
    NUMBER_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
)
from custom_components.solem_blip.number import (
    IrrigationManualDuration,
    async_setup_entry as setup_number,
)
from custom_components.solem_blip.sensor import (
    BatterySensor,
    BatteryVoltageSensor,
    LastTimeSyncSensor,
    ProgramNameSensor,
    ProgramNextStartSensor,
    ProgramScheduleSensor,
    RemainingSprinkleSensor,
    StateSensor,
    async_setup_entry as setup_sensor,
)
from custom_components.solem_blip.coordinator import SolemCoordinator
from tests.conftest import MOCK_IRRIGATION_PROGRAMS


def _seed_coordinator_state(coordinator: SolemCoordinator) -> None:
    """Populate coordinator fields used by descriptor builders in tests."""
    coordinator.battery_level = 5
    coordinator.battery_voltage = 90
    coordinator.controller.state = "on"
    coordinator.stations[0].state = "stopped"
    coordinator._has_status = True
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)


async def _setup_platform(
    hass: HomeAssistant,
    coordinator: SolemCoordinator,
    mock_config_entry: MockConfigEntry,
    setup_fn,
) -> list:
    mock_config_entry.runtime_data = RuntimeData(coordinator, MagicMock())
    entities: list = []
    await setup_fn(hass, mock_config_entry, entities.extend)
    return entities


@pytest.mark.asyncio
async def test_sensor_platform_creates_entities(
    hass: HomeAssistant, coordinator: SolemCoordinator, mock_config_entry: MockConfigEntry
) -> None:
    """Sensor platform registers all coordinator sensor descriptors."""
    entities = await _setup_platform(hass, coordinator, mock_config_entry, setup_sensor)
    assert any(isinstance(entity, StateSensor) for entity in entities)
    assert any(isinstance(entity, BatterySensor) for entity in entities)
    assert any(isinstance(entity, BatteryVoltageSensor) for entity in entities)
    assert any(isinstance(entity, RemainingSprinkleSensor) for entity in entities)
    assert any(isinstance(entity, LastTimeSyncSensor) for entity in entities)
    assert any(isinstance(entity, ProgramNameSensor) for entity in entities)
    assert any(isinstance(entity, ProgramNextStartSensor) for entity in entities)
    assert any(isinstance(entity, ProgramScheduleSensor) for entity in entities)



@pytest.mark.asyncio
async def test_sensor_values_and_attributes(
    hass: HomeAssistant,
    coordinator: SolemCoordinator,
) -> None:
    """Sensor entities expose coordinator-backed values."""
    _seed_coordinator_state(coordinator)
    coordinator.data = await coordinator.async_update_all_sensors(fetch_status=False)
    battery_device = next(
        item for item in coordinator.data if item["device_type"] == "BATTERY_SENSOR"
    )
    battery = BatterySensor(
        coordinator, battery_device, "state", SENSOR_DESCRIPTIONS["BATTERY_SENSOR"]
    )
    assert battery.native_value == 100
    assert battery.extra_state_attributes["battery_level"] == 5

    program_device = next(
        item for item in coordinator.data if item["device_type"] == "PROGRAM_NEXT_START_SENSOR"
    )
    program_next = ProgramNextStartSensor(
        coordinator,
        program_device,
        "state",
        SENSOR_DESCRIPTIONS["PROGRAM_NEXT_START_SENSOR"],
    )
    attrs = program_next.extra_state_attributes
    assert "minutes_since_midnight" in attrs

    schedule_device = next(
        item for item in coordinator.data if item["device_type"] == "PROGRAM_SCHEDULE_SENSOR"
    )
    schedule = ProgramScheduleSensor(
        coordinator,
        schedule_device,
        "state",
        SENSOR_DESCRIPTIONS["PROGRAM_SCHEDULE_SENSOR"],
    )
    assert schedule.native_value is not None
    assert schedule.extra_state_attributes


@pytest.mark.asyncio
async def test_program_sensor_subscribes_to_schedule_coordinator(
    hass: HomeAssistant,
    coordinator: SolemCoordinator,
) -> None:
    """Program sensors trigger the first background schedule refresh."""
    device = next(
        item for item in coordinator.data if item["device_type"] == "PROGRAM_NAME_SENSOR"
    )
    sensor = ProgramNameSensor(
        coordinator,
        device,
        "state",
        SENSOR_DESCRIPTIONS["PROGRAM_NAME_SENSOR"],
    )
    sensor.hass = hass
    remove_listener = MagicMock()
    coordinator.schedule_coordinator.async_add_listener = MagicMock(
        return_value=remove_listener
    )
    coordinator.schedule_coordinator.async_start_first_refresh = MagicMock()

    with patch(
        "custom_components.solem_blip.sensor.SolemSensorEntity.async_added_to_hass",
        new=AsyncMock(),
    ):
        await sensor.async_added_to_hass()

    coordinator.schedule_coordinator.async_add_listener.assert_called_once()
    coordinator.schedule_coordinator.async_start_first_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_binary_sensor_platform(
    hass: HomeAssistant, coordinator: SolemCoordinator, mock_config_entry: MockConfigEntry
) -> None:
    """Binary sensor platform exposes battery low and program running sensors."""
    entities = await _setup_platform(hass, coordinator, mock_config_entry, setup_binary)
    assert len(entities) == 4
    battery_entities = [e for e in entities if e.__class__.__name__ == "BatteryLow"]
    assert len(battery_entities) == 1
    coordinator.battery_low = False
    assert battery_entities[0].is_on is False


@pytest.mark.asyncio
async def test_number_platform_set_value(
    hass: HomeAssistant, coordinator: SolemCoordinator, mock_config_entry: MockConfigEntry
) -> None:
    """Number platform updates manual irrigation duration."""
    entities = await _setup_platform(hass, coordinator, mock_config_entry, setup_number)
    assert len(entities) == 1
    number = entities[0]
    assert isinstance(number, IrrigationManualDuration)
    assert number.native_value == 10.0
    number.hass = hass
    number.async_write_ha_state = MagicMock()
    await number.async_set_native_value(15.0)
    assert coordinator.irrigation_manual_duration == 15


@pytest.mark.asyncio
async def test_button_platform_and_press_actions(
    hass: HomeAssistant,
    coordinator: SolemCoordinator,
    mock_config_entry: MockConfigEntry,
    mock_solem_client,
) -> None:
    """Button platform registers actions and surfaces translated BLE errors."""
    entities = await _setup_platform(hass, coordinator, mock_config_entry, setup_button)
    by_type = {entity.__class__ for entity in entities}
    assert IrrigationStartButton in by_type
    assert IrrigationStopButton in by_type
    assert ControllerOnButton in by_type
    assert ControllerOffButton in by_type

    stop_button = next(entity for entity in entities if isinstance(entity, IrrigationStopButton))
    await stop_button.async_press()
    mock_solem_client.stop_manual_sprinkle.assert_awaited()

    on_button = next(entity for entity in entities if isinstance(entity, ControllerOnButton))
    await on_button.async_press()
    mock_solem_client.turn_on.assert_awaited()

    off_button = next(entity for entity in entities if isinstance(entity, ControllerOffButton))
    await off_button.async_press()
    mock_solem_client.turn_off_permanent.assert_awaited()

    start_button = next(
        entity for entity in entities if isinstance(entity, IrrigationStartButton)
    )
    mock_solem_client.sprinkle_station_x_for_y_minutes = AsyncMock(
        side_effect=APIConnectionError("fail")
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        await start_button.async_press()
    assert exc_info.value.translation_key == "start_irrigation_failed"


@pytest.mark.asyncio
async def test_entity_coordinator_update_refreshes_placeholders(
    hass: HomeAssistant,
    coordinator: SolemCoordinator,
) -> None:
    """Coordinator updates refresh translation placeholders on entities."""
    device = next(
        item
        for item in coordinator.data
        if item["device_id"].endswith("_irrigation_station_1_status")
    )
    entity = StateSensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["STATE_SENSOR"]
    )
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    coordinator.station_names[1] = "Lawn"
    updated_device = dict(device)
    updated_device["translation_placeholders"] = {"station_name": "Lawn"}
    coordinator.data = [
        updated_device if item["device_id"] == device["device_id"] else item
        for item in coordinator.data
    ]
    entity._handle_coordinator_update()
    assert entity._attr_translation_placeholders == {"station_name": "Lawn"}


@pytest.mark.asyncio
async def test_device_info_includes_bluetooth_connection(
    coordinator: SolemCoordinator,
) -> None:
    """Device info links the controller over Bluetooth."""
    device = next(item for item in coordinator.data if item["device_type"] == "BATTERY_SENSOR")
    entity = BatterySensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["BATTERY_SENSOR"]
    )
    info = entity.device_info
    assert ("bluetooth", "AA:BB:CC:DD:EE:FF") in info["connections"]
