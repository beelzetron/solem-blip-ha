"""The coordinator's program write day follows Home Assistant's time zone."""

from __future__ import annotations

from datetime import date, datetime
from typing import cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from solem_blip_ble import IrrigationProgram

from custom_components.solem_blip import coordinator as coordinator_module
from custom_components.solem_blip.coordinator import SolemCoordinator

PROGRAM = {
    "name": "Programme A",
    "inter_station_delay": 0,
    "water_budget": 100,
    "cycle": 4,
    "week_days": 0x7F,
    "period_length": 3,
    "synchro_day": 1,
    "period_start_date": None,
    "start_times": [360, None, None, None, None, None, None, None],
    "station_durations": [600, 600],
}


@pytest.mark.asyncio
async def test_set_irrigation_program_passes_ha_local_write_day(
    coordinator: SolemCoordinator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #77: the write day comes from HA's clock, not the process clock.

    The library defaults to ``date.today()`` (the process time zone). A caller
    that keeps its dates in another zone passes its own day, so the packed
    header date and the derived countdown describe the day the editor preview
    shows. Home Assistant is such a caller.
    """
    ha_now = datetime(2026, 10, 22, 23, 30, tzinfo=ZoneInfo("US/Pacific"))
    monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: ha_now)
    monkeypatch.setattr(
        coordinator, "async_update_all_sensors", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        coordinator.program_backup, "async_save_if_non_empty", AsyncMock()
    )
    monkeypatch.setattr(coordinator.display_names, "async_save", AsyncMock())

    await coordinator.set_irrigation_program(1, cast(IrrigationProgram, PROGRAM))

    assert coordinator.api.set_irrigation_program.await_args.kwargs["today"] == date(
        2026, 10, 22
    )
