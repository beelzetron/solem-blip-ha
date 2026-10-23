"""Regression guard: the program editor's "today" comes from HA's clock."""

from datetime import date, timedelta

import pytest

import custom_components.solem_blip.config_flow as config_flow
from homeassistant.util import dt as dt_util


def test_editor_today_follows_ha_local_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process clock one day behind HA must not shift the editor's today.

    The nightly CI run failed exactly this way: the runner's system date and
    Home Assistant's configured time zone disagreed, so every direct write
    turned into a spurious wrap confirm.
    """
    ha_today = dt_util.now().date()

    class _SkewedDate(date):
        @classmethod
        def today(cls) -> date:
            return ha_today - timedelta(days=1)

    monkeypatch.setattr(config_flow, "date", _SkewedDate)

    options = config_flow._period_start_date_options(3)
    assert options[0] == config_flow._period_label(ha_today)

    anchor = ha_today + timedelta(days=5)
    assert config_flow._countdown_for_anchor(4, anchor, 3) == 2

    handler = config_flow.SolemOptionsFlowHandler()
    defaults = handler._program_defaults(
        {
            "cycle": 4,
            "period_length": 3,
            "synchro_day": 2,
            "start_times": [390, None, None, None, None, None, None, None],
            "station_durations": [0, 1200],
            "week_days": 0x7F,
            "water_budget": 100,
            "inter_station_delay": 0,
        },
        num_stations=2,
    )
    assert defaults[config_flow.ATTR_PERIOD_START_DATE] == ha_today + timedelta(days=2)
