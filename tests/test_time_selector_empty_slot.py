"""Regression tests for the time-selector empty-slot fix (issue #130 follow-up).

The frontend submits an untouched/cleared time selector as an empty string.
The bare TimeSelector rejects ``''`` with ``Invalid time specified``, which
made the whole program-edit form unsubmittable whenever any start slot was
empty — the normal case, since most programs use 1-2 of the 8 slots.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import PropertyMock, patch

import homeassistant.helpers.config_validation as cv
import pytest
import voluptuous as vol
from probatio import to_field_list

from custom_components.solem_blip.config_flow import (
    _StartSlotSelector,
    SolemOptionsFlowHandler,
)
from tests.conftest import MOCK_IRRIGATION_PROGRAMS


def _schema_for_two_starts() -> vol.Schema:
    """Build the start-slot portion of the program-edit schema directly."""
    defaults: dict[str, Any] = {f"start_time_{i}": "" for i in range(1, 9)}
    defaults["start_time_1"] = "06:30"
    fields: dict[Any, Any] = {}
    for slot in range(8):
        key = SolemOptionsFlowHandler._start_key(slot)
        fields[vol.Optional(key, default=defaults[key])] = _StartSlotSelector(
            {}
        )
    return vol.Schema(fields)


def test_cleared_start_slot_validates_to_empty_string() -> None:
    """An empty (cleared/disabled) slot must pass validation as ''."""
    schema = _schema_for_two_starts()
    result = schema({"start_time_1": "", "start_time_2": "17:30:00"})
    assert result["start_time_1"] == ""
    assert result["start_time_2"] == "17:30"
    # Absent keys fall back to the schema defaults (form partial submit).
    fallback = schema({"start_time_2": "17:30:00"})
    assert fallback["start_time_1"] == "06:30"


def test_seconds_are_stripped_from_submitted_values() -> None:
    """Submitted seconds must be stripped: the picker must not show them.

    HA's time input shows a seconds field only when the value carries
    ":SS" — normalizing to HH:MM keeps the picker seconds-free.
    """
    schema = _schema_for_two_starts()
    result = schema({"start_time_1": "17:30:45"})
    assert result["start_time_1"] == "17:30"


def test_defaults_are_seconds_free() -> None:
    """_format_minutes defaults must stay HH:MM (no seconds)."""
    assert SolemOptionsFlowHandler._format_minutes(390) == "06:30"
    assert SolemOptionsFlowHandler._format_minutes(0) == "00:00"
    assert SolemOptionsFlowHandler._format_minutes(None) == ""


def test_filled_start_slot_still_validates() -> None:
    """Real times (picker 'HH:MM:SS' or legacy 'HH:MM') still validate."""
    schema = _schema_for_two_starts()
    result = schema({"start_time_1": "05:00", "start_time_2": "17:30:45"})
    assert result["start_time_1"] == "05:00"
    assert result["start_time_2"] == "17:30"


def test_invalid_time_still_rejected() -> None:
    """A malformed non-empty time must still be rejected."""
    schema = _schema_for_two_starts()
    with pytest.raises(vol.Invalid):
        schema({"start_time_1": "25:99"})


def test_start_slot_selector_serializes_like_plain_time_selector(
    mock_config_entry: Any,
) -> None:
    """The empty-tolerant selector must render as a normal time picker."""
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = to_field_list(
            handler._program_schema(MOCK_IRRIGATION_PROGRAMS[0]),
            custom_serializer=cv.custom_serializer,
        )

    start_fields = [
        field for field in serialized if field["name"].startswith("start_time_")
    ]
    assert len(start_fields) == 8
    for field in start_fields:
        assert field["selector"] == {"time": {}}
