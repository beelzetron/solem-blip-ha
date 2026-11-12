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
    r"|battery_low|time_alarm|program_running)$"
)


def normalize_entity_state(value: str | None) -> str:
    """Normalize controller/station state strings for HA entity state."""
    if not value:
        return "unknown"
    return value.strip().lower().replace(" ", "_")


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_MAC_SEPARATORS = (":", "-", " ")


def normalize_controller_mac(value: object) -> str | None:
    """Return the canonical MAC address from free-form user input, or None.

    Accepts colon-separated pairs (``AA:BB:CC:DD:EE:FF``), hyphen-separated
    pairs (``aa-bb-cc-dd-ee-ff``), space-separated pairs
    (``AA BB CC DD EE FF``), a bare 12-digit form (``AABBCCDDEEFF``),
    surrounding whitespace, and a pasted ``"<name> - <MAC>"`` string (the
    part after the last ``" - "`` is used, matching the entry-data shape the
    coordinator parses).

    Only complete, unambiguous addresses are accepted: a partial match, a
    mixed separator, a wrong length or a non-hex character yields None. The
    parameter is annotated ``object`` because callers pass raw user input;
    any non-string value yields None instead of raising.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if " - " in text:
        text = text.rsplit(" - ", 1)[1].strip()

    if len(text) == 12:
        digits = text
    elif len(text) == 17:
        separator = text[2]
        if separator not in _MAC_SEPARATORS:
            return None
        if any(text[index] != separator for index in (5, 8, 11, 14)):
            return None
        digits = text.replace(separator, "")
    else:
        return None

    if not all(character in _HEX_DIGITS for character in digits):
        return None
    pairs = [digits[index : index + 2] for index in range(0, 12, 2)]
    return ":".join(pair.upper() for pair in pairs)


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
