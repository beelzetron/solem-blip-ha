"""Regression tests for the time-selector empty-slot fix (issue #130 follow-up).

The frontend submits an untouched/cleared time selector as an empty string.
The bare TimeSelector rejects ``''`` with ``Invalid time specified``, which
made the whole program-edit form unsubmittable whenever any start slot was
empty — the normal case, since most programs use 1-2 of the 8 slots.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from homeassistant.helpers.selector import selector

from custom_components.solem_blip.config_flow import (
    _START_TIME_EMPTY,
    SolemOptionsFlowHandler,
)


def _start_time_field() -> Any:
    """The start-slot schema validator: empty allowed, else time selector."""
    return vol.Any(_START_TIME_EMPTY, selector({"time": {}}))


def _schema_for_two_starts() -> vol.Schema:
    """Build the start-slot portion of the program-edit schema directly."""
    defaults: dict[str, Any] = {f"start_time_{i}": "" for i in range(1, 9)}
    defaults["start_time_1"] = "06:30"
    fields: dict[Any, Any] = {}
    for slot in range(8):
        key = SolemOptionsFlowHandler._start_key(slot)
        fields[vol.Optional(key, default=defaults[key])] = _start_time_field()
    return vol.Schema(fields)


def test_cleared_start_slot_validates_to_empty_string() -> None:
    """An empty (cleared/disabled) slot must pass validation as ''."""
    schema = _schema_for_two_starts()
    result = schema({"start_time_1": "", "start_time_2": "17:30:00"})
    assert result["start_time_1"] == ""
    assert result["start_time_2"] == "17:30:00"
    # Absent keys fall back to the schema defaults (form partial submit).
    fallback = schema({"start_time_2": "17:30:00"})
    assert fallback["start_time_1"] == "06:30"


def test_filled_start_slot_still_validates() -> None:
    """Real times (picker 'HH:MM:SS' or legacy 'HH:MM') still validate."""
    schema = _schema_for_two_starts()
    result = schema({"start_time_1": "05:00", "start_time_2": "17:30:45"})
    assert result["start_time_1"] == "05:00"
    assert result["start_time_2"] == "17:30:45"


def test_invalid_time_still_rejected() -> None:
    """A malformed non-empty time must still be rejected."""
    schema = _schema_for_two_starts()
    with pytest.raises(vol.Invalid):
        schema({"start_time_1": "25:99"})
