"""Shared helpers for the Solem BL-IP integration."""

from __future__ import annotations

import re

from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN

_LEGACY_UNIQUE_ID_RE = re.compile(
    r"^"
    + re.escape(DOMAIN)
    + r"-[0-9A-Fa-f:]{11,17}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-\d{3,4}-"
    r"(?:state|value|sprinkle_station|stop_sprinkle|controller_on|controller_off"
    r"|battery_low|program_running)$"
)


def normalize_entity_state(value: str | None) -> str:
    """Normalize controller/station state strings for HA entity state."""
    if not value:
        return "unknown"
    return value.strip().lower().replace(" ", "_")


def mac_to_uuid(mac: str, last_part: int) -> str:
    """Build the legacy numeric device_uid segment used in older unique IDs."""
    mac_numbers = mac.replace(":", "").upper()
    x_part = f"{mac_numbers[:4]}-{mac_numbers[4:8]}-{mac_numbers[8:12]}"
    yyy_part = f"{last_part:03d}"
    return f"{x_part}-{yyy_part}"


def format_entity_unique_id(mac: str, device_id: str) -> str:
    """Return a stable entity unique_id from MAC and descriptor device_id."""
    formatted_mac = format_mac(mac)
    prefix = f"{formatted_mac}_"
    if device_id.startswith(prefix):
        suffix = device_id[len(prefix) :]
    elif device_id.startswith(f"{mac}_"):
        suffix = device_id[len(mac) + 1 :]
    else:
        suffix = device_id
    return f"{DOMAIN}-{formatted_mac}-{suffix}"


def format_legacy_unique_id(mac: str, device_uid: str, suffix: str) -> str:
    """Return the pre-1.2.3 counter-based unique_id for registry migration."""
    return f"{DOMAIN}-{mac}-{device_uid}-{suffix}"


def is_legacy_unique_id(unique_id: str | None) -> bool:
    """Return True when unique_id matches the deprecated counter-based format."""
    if not unique_id:
        return False
    return _LEGACY_UNIQUE_ID_RE.match(unique_id) is not None
