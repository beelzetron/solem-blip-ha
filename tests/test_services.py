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
from solem_blip_ble.exceptions import SolemConnectionError, SolemDeadlineExceeded

from custom_components.solem_blip import RuntimeData
from custom_components.solem_blip.const import DOMAIN
from custom_components.solem_blip.coordinator import SolemCoordinator
from custom_components.solem_blip.services import (
    SERVICE_REFRESH_PROGRAMS,
    SERVICE_RESTORE_PROGRAMS,
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
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ), patch(
        "custom_components.solem_blip.bluetooth.async_get_connectable_device",
    ):
        coordinator = SolemCoordinator(hass, mock_config_entry)
        await coordinator.async_init()

    mock_config_entry.add_to_hass(hass)
    mock_config_entry.runtime_data = RuntimeData(coordinator)
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
    assert hass.services.has_service(DOMAIN, SERVICE_RESTORE_PROGRAMS)

    async_unload_services(hass)

    assert not hass.services.has_service(DOMAIN, SERVICE_SET_PROGRAM)
    assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH_PROGRAMS)
    assert not hass.services.has_service(DOMAIN, SERVICE_RESTORE_PROGRAMS)


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



@pytest.mark.asyncio
async def test_restore_programs_service_replays_backup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """restore_programs replays each backed-up slot through the coordinator."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    coordinator.schedule_coordinator.async_set_updated_data = MagicMock()
    programs = {
        0: {
            "name": "Morning",
            "inter_station_delay": 0,
            "water_budget": 100,
            "cycle": 0,
            "week_days": 0x7F,
            "period_length": 1,
            "synchro_day": 0,
            "period_start_date": date(2026, 6, 1),
            "start_times": [360, None, None, None, None, None, None, None],
            "station_durations": [60, 120],
        }
    }
    await coordinator.program_backup.async_save_if_non_empty(programs)
    mock_solem_client.write_irrigation_program = AsyncMock()
    mock_solem_client.get_irrigation_config = AsyncMock(return_value=programs)

    with patch("custom_components.solem_blip.coordinator.asyncio.sleep", new=AsyncMock()):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

    mock_solem_client.write_irrigation_program.assert_awaited_once_with(0, programs[0])
    mock_solem_client.get_irrigation_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_programs_service_rejects_missing_backup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """restore_programs fails clearly when no snapshot exists."""
    _, device_id = await _setup_service_target(hass, mock_config_entry, mock_solem_client)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

    assert not mock_solem_client.write_irrigation_program.called


@pytest.mark.asyncio
async def test_restore_programs_service_rejects_active_watering(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Backups cannot be restored while watering is active."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    await coordinator.program_backup.async_save_if_non_empty(
        {
            0: {
                "name": "Morning",
                "inter_station_delay": 0,
                "water_budget": 100,
                "cycle": 0,
                "week_days": 0x7F,
                "period_length": 1,
                "synchro_day": 0,
                "period_start_date": None,
                "start_times": [360, None, None, None, None, None, None, None],
                "station_durations": [60, 120],
            }
        }
    )
    coordinator._is_watering = True

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

    assert not mock_solem_client.write_irrigation_program.called


@pytest.mark.asyncio
async def test_restore_programs_service_wraps_write_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """BLE restore errors are surfaced as service errors."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    await coordinator.program_backup.async_save_if_non_empty(
        {
            0: {
                "name": "Morning",
                "inter_station_delay": 0,
                "water_budget": 100,
                "cycle": 0,
                "week_days": 0x7F,
                "period_length": 1,
                "synchro_day": 0,
                "period_start_date": None,
                "start_times": [360, None, None, None, None, None, None, None],
                "station_durations": [60, 120],
            }
        }
    )
    mock_solem_client.write_irrigation_program = AsyncMock(side_effect=RuntimeError)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

@pytest.mark.asyncio
async def test_restore_programs_uses_independent_final_verification(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Immediate verification failures do not decide complete restore success."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    coordinator.schedule_coordinator.async_set_updated_data = MagicMock()
    programs = {
        1: {
            "name": "Programme B",
            "inter_station_delay": 0,
            "water_budget": 100,
            "cycle": 4,
            "week_days": 0x7F,
            "period_length": 3,
            "synchro_day": 1,
            "period_start_date": date(2026, 9, 22),
            "start_times": [360, None, None, None, None, None, None, None],
            "station_durations": [600, 600],
        }
    }
    await coordinator.program_backup.async_save_if_non_empty(programs)
    mock_solem_client.write_irrigation_program = AsyncMock()
    mock_solem_client.get_irrigation_config = AsyncMock(return_value=programs)

    with patch("custom_components.solem_blip.coordinator.asyncio.sleep", new=AsyncMock()):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

    mock_solem_client.write_irrigation_program.assert_awaited_once_with(1, programs[1])
    mock_solem_client.get_irrigation_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_programs_does_not_retry_slots_failing_final_verification(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """A stale final read fails without adding more BLE write retries."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    coordinator.schedule_coordinator.async_set_updated_data = MagicMock()
    programs = {
        1: {
            "name": "Programme B",
            "inter_station_delay": 0,
            "water_budget": 100,
            "cycle": 4,
            "week_days": 0x7F,
            "period_length": 3,
            "synchro_day": 1,
            "period_start_date": date(2026, 9, 22),
            "start_times": [360, None, None, None, None, None, None, None],
            "station_durations": [600, 600],
        },
        2: {
            "name": "Programme C",
            "inter_station_delay": 0,
            "water_budget": 100,
            "cycle": 4,
            "week_days": 0x7F,
            "period_length": 7,
            "synchro_day": 5,
            "period_start_date": date(2026, 9, 22),
            "start_times": [1200, None, None, None, None, None, None, None],
            "station_durations": [0, 1200],
        },
    }
    stale = {
        **programs,
        2: {
            **programs[2],
            "name": "test 2",
            "cycle": 0,
            "week_days": 0,
            "synchro_day": 0,
            "start_times": [None, None, None, None, None, None, None, None],
        },
    }
    await coordinator.program_backup.async_save_if_non_empty(programs)
    mock_solem_client.write_irrigation_program = AsyncMock()
    mock_solem_client.get_irrigation_config = AsyncMock(return_value=stale)

    with (
        patch("custom_components.solem_blip.coordinator.asyncio.sleep", new=AsyncMock()),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

    assert mock_solem_client.write_irrigation_program.await_count == 2
    assert mock_solem_client.get_irrigation_config.await_count == 1


@pytest.mark.asyncio
async def test_restore_programs_fails_if_final_retry_is_not_persisted(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Restore reports failure when the controller remains stale after retry."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    programs = {
        2: {
            "name": "Programme C",
            "inter_station_delay": 0,
            "water_budget": 100,
            "cycle": 4,
            "week_days": 0x7F,
            "period_length": 7,
            "synchro_day": 5,
            "period_start_date": date(2026, 9, 22),
            "start_times": [1200, None, None, None, None, None, None, None],
            "station_durations": [0, 1200],
        }
    }
    stale = {
        2: {
            **programs[2],
            "name": "test 2",
            "cycle": 0,
            "week_days": 0,
            "synchro_day": 0,
            "start_times": [None, None, None, None, None, None, None, None],
        }
    }
    await coordinator.program_backup.async_save_if_non_empty(programs)
    mock_solem_client.write_irrigation_program = AsyncMock()
    mock_solem_client.get_irrigation_config = AsyncMock(return_value=stale)

    with (
        patch("custom_components.solem_blip.coordinator.asyncio.sleep", new=AsyncMock()),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

    assert mock_solem_client.write_irrigation_program.await_count == 1
    assert mock_solem_client.get_irrigation_config.await_count == 1


@pytest.mark.asyncio
async def test_restore_programs_writes_scheduled_slots_before_empty_slots(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client: MagicMock,
) -> None:
    """Useful scheduled programs are restored before empty/default slots."""
    coordinator, device_id = await _setup_service_target(
        hass, mock_config_entry, mock_solem_client
    )
    coordinator.schedule_coordinator.async_set_updated_data = MagicMock()
    programs = {
        0: {
            "name": "Programme A",
            "inter_station_delay": 0,
            "water_budget": 100,
            "cycle": 0,
            "week_days": 0x7F,
            "period_length": 2,
            "synchro_day": 0,
            "period_start_date": date(2026, 9, 22),
            "start_times": [None, None, None, None, None, None, None, None],
            "station_durations": [60, 0],
        },
        1: {
            "name": "Programme B",
            "inter_station_delay": 0,
            "water_budget": 100,
            "cycle": 4,
            "week_days": 0x7F,
            "period_length": 3,
            "synchro_day": 1,
            "period_start_date": date(2026, 9, 22),
            "start_times": [360, None, None, None, None, None, None, None],
            "station_durations": [600, 600],
        },
    }
    await coordinator.program_backup.async_save_if_non_empty(programs)
    mock_solem_client.write_irrigation_program = AsyncMock()
    mock_solem_client.get_irrigation_config = AsyncMock(return_value=programs)

    with patch("custom_components.solem_blip.coordinator.asyncio.sleep", new=AsyncMock()):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROGRAMS,
            {"device_id": device_id},
            blocking=True,
        )

    assert [
        call.args[0]
        for call in mock_solem_client.write_irrigation_program.await_args_list
    ] == [1, 0]



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
