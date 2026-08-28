"""Typed config entry helpers for the Solem BL-IP integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator


@dataclass
class RuntimeData:
    """Runtime data attached to a config entry."""

    coordinator: SolemCoordinator


type MyConfigEntry = ConfigEntry[RuntimeData]
