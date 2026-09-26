"""Map BLE library exceptions to config-flow error keys.

Kept separate from the flow logic so both the program editor and the
station-name editor share one exception-to-message mapping without
touching shared flow code.
"""

from __future__ import annotations

import asyncio

from solem_blip_ble.exceptions import (
    InvalidSnapshot,
    SolemConnectionError,
    StaleProgram,
    UncertainWrite,
)


def flow_error_for_exception(exc: Exception) -> str:
    """Return the options-flow error key for a station-name write failure."""
    if isinstance(exc, StaleProgram):
        return "stale_station_name"
    if isinstance(exc, UncertainWrite):
        return "station_name_uncertain"
    if isinstance(exc, InvalidSnapshot):
        return "station_name_busy"
    if isinstance(exc, SolemConnectionError):
        return "station_name_failed"
    return "station_name_failed"

