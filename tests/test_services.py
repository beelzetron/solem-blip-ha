"""Service tests for Solem BL-IP schedule management."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import RuntimeData
from custom_components.solem_blip.const import DOMAIN
from custom_components.solem_blip.coordinator import SolemCoordinator
from custom_components.solem_blip.services import (
    SERVICE_REFRESH_PROGRAMS,
    SERVICE_SET_PROGRAM,
    async_unload_services,
    async_setup_services,
    _program_from_service_data,
)


async def _setup_service_target(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> tuple[SolemCoordinator, str]:
    with patch(
        "custom_components.solem_blip.coordinator.SolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()

    mock_config_entry.add_to_hass(hass)
    mock_config_entry.runtime_data = RuntimeData(coordinator, None)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, coordinator.controller_mac_address)},
    )
    await async_setup_services(hass)
    return coordinator, device.id


@pytest.mark.asyncio
async def test_set_program_service_writes_normalized_program(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """set_program resolves the device target and writes a normalized program."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    coordinator.schedule_coordinator.async_set_updated_data = MagicMock()
    mock_solem_client.set_irrigation_program = AsyncMock(
        return_value={
            **coordinator.irrigation_programs,
            1: {
                "name": "Evening",
                "inter_station_delay": 5,
                "water_budget": 80,
                "cycle": 0,
                "week_days": 0x05,
                "period_length": 1,
                "synchro_day": 0,
                "period_start_date": date(2026, 6, 1),
                "start_times": [360, 1110, None, None, None, None, None, None],
                "station_durations": [60, 120],
            },
        }
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_PROGRAM,
        {
            "device_id": device_id,
            "program": 2,
            "name": "Evening",
            "cycle": "custom",
            "week_days": ["monday", "wednesday"],
            "period_start_date": date(2026, 6, 1),
            "start_times": ["06:00", "18:30"],
            "station_durations": {"1": 60, "2": 120},
            "inter_station_delay": 5,
            "water_budget": 80,
        },
        blocking=True,
    )

    mock_solem_client.set_irrigation_program.assert_awaited_once_with(
        1,
        {
            "name": "Evening",
            "inter_station_delay": 5,
            "water_budget": 80,
            "cycle": 0,
            "week_days": 0x05,
            "period_length": 1,
            "synchro_day": 0,
            "period_start_date": date(2026, 6, 1),
            "start_times": [360, 1110, None, None, None, None, None, None],
            "station_durations": [60, 120],
        },
    )


@pytest.mark.asyncio
async def test_set_program_service_rejects_active_watering(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Schedules cannot be changed while watering is active."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    coordinator._is_watering = True

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PROGRAM,
            {
                "device_id": device_id,
                "program": 1,
                "name": "Blocked",
                "start_times": ["06:00"],
                "station_durations": {"1": 60},
            },
            blocking=True,
        )

    mock_solem_client.set_irrigation_program.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_programs_service_requests_schedule_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """refresh_programs forces the slow schedule coordinator to refresh."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    coordinator.schedule_coordinator.async_request_refresh = AsyncMock()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REFRESH_PROGRAMS,
        {"device_id": device_id},
        blocking=True,
    )

    assert coordinator._irrigation_config_refresh_after == 0.0
    coordinator.schedule_coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_services_register_once_and_unload(hass: HomeAssistant) -> None:
    """Service setup is idempotent and unload removes registered services."""
    await async_setup_services(hass)
    await async_setup_services(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_SET_PROGRAM)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_PROGRAMS)

    async_unload_services(hass)

    assert not hass.services.has_service(DOMAIN, SERVICE_SET_PROGRAM)
    assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH_PROGRAMS)


@pytest.mark.asyncio
async def test_refresh_programs_service_rejects_unknown_device(
    hass: HomeAssistant,
) -> None:
    """Services fail clearly when the selected device id is not registered."""
    await async_setup_services(hass)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH_PROGRAMS,
            {"device_id": "missing"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_set_program_service_wraps_write_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """BLE write errors are surfaced as service errors."""
    _, device_id = await _setup_service_target(hass, mock_config_entry, mock_solem_client)
    mock_solem_client.set_irrigation_program = AsyncMock(side_effect=RuntimeError)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PROGRAM,
            {
                "device_id": device_id,
                "program": 1,
                "name": "Failure",
                "start_times": ["06:00"],
                "station_durations": {"1": 60},
            },
            blocking=True,
        )


def test_program_service_data_validation_errors() -> None:
    """Structured program service data rejects malformed values."""
    base = {
        "name": "Morning",
        "cycle": "custom",
        "week_days": ["monday"],
        "period_length": 1,
        "synchro_day": 0,
        "inter_station_delay": 0,
        "water_budget": 100,
        "period_start_date": "2026-06-01",
    }

    assert _program_from_service_data(
        {
            **base,
            "week_days": [0, "sunday"],
            "start_times": ["06:00"],
            "station_durations": {"1": 60},
        },
        num_stations=1,
    )["week_days"] == 0x41

    for bad_data in (
        {**base, "start_times": ["bad"], "station_durations": {"1": 60}},
        {**base, "start_times": ["24:00"], "station_durations": {"1": 60}},
        {**base, "week_days": ["funday"], "start_times": ["06:00"], "station_durations": {"1": 60}},
        {**base, "week_days": [7], "start_times": ["06:00"], "station_durations": {"1": 60}},
        {**base, "start_times": ["06:00"], "station_durations": {"2": 60}},
        {**base, "start_times": ["06:00"], "station_durations": {"1": -1}},
        {
            **base,
            "start_times": ["06:00"] * 9,
            "station_durations": {"1": 60},
        },
    ):
        with pytest.raises(vol.Invalid):
            _program_from_service_data(bad_data, num_stations=1)
