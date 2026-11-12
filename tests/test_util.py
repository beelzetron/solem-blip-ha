"""Utility helper tests."""

from __future__ import annotations

import pytest

from custom_components.solem_blip.util import (
    format_entity_unique_id,
    is_legacy_unique_id,
    normalize_controller_mac,
    normalize_entity_state,
)

CANONICAL_MAC = "AA:BB:CC:DD:EE:FF"


def test_normalize_entity_state() -> None:
    """Unknown and blank values normalize to unknown."""
    assert normalize_entity_state(None) == "unknown"
    assert normalize_entity_state(" On ") == "on"


def test_format_entity_unique_id_without_mac_prefix() -> None:
    """Device IDs without a MAC prefix still produce a stable unique_id."""
    assert format_entity_unique_id("AA:BB:CC:DD:EE:FF", "standalone_id").endswith(
        "-standalone_id"
    )


def test_is_legacy_unique_id_handles_empty() -> None:
    """Legacy detector rejects missing values."""
    assert is_legacy_unique_id(None) is False
    assert is_legacy_unique_id("") is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AA:BB:CC:DD:EE:FF", CANONICAL_MAC),
        ("aa:bb:cc:dd:ee:ff", CANONICAL_MAC),
        ("aa-bb-cc-dd-ee-ff", CANONICAL_MAC),
        ("AA-BB-CC-DD-EE-FF", CANONICAL_MAC),
        ("AA BB CC DD EE FF", CANONICAL_MAC),
        ("aa bb cc dd ee ff", CANONICAL_MAC),
        ("  AA BB CC DD EE FF  ", CANONICAL_MAC),
        ("AABBCCDDEEFF", CANONICAL_MAC),
        ("aabbccddeeff", CANONICAL_MAC),
        ("  aa:bb:cc:dd:ee:ff\n", CANONICAL_MAC),
        ("Solem BL-IP - aa:bb:cc:dd:ee:ff", CANONICAL_MAC),
        ("Solem BL-IP - AA BB CC DD EE FF", CANONICAL_MAC),
        ("BL1IP-A1B2C3 - AABBCCDDEEFF", CANONICAL_MAC),
        ("Something - Else - aa-bb-cc-dd-ee-ff", CANONICAL_MAC),
        ("  BL1IP-A1B2C3 - aabbccddeeff  ", CANONICAL_MAC),
    ],
)
def test_normalize_controller_mac_accepts_supported_formats(
    raw: str, expected: str
) -> None:
    """Supported formats normalize to the canonical uppercase MAC."""
    assert normalize_controller_mac(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "AA:BB:CC:DD:EE",
        "AA:BB:CC:DD:EE:FF:00",
        "AA BB CC DD EE",
        "AA BB CC DD EE FF 00",
        "AABBCCDDEEFF00",
        "GG:BB:CC:DD:EE:FF",
        "AA:BB-CC:DD:EE:FF",
        "AA BB-CC DD EE FF",
        "AA.BB.CC.DD.EE.FF",
        "AA:BB:CC:DD:EE:F",
        "AA:BB:CC:DD:EE:FF:",
        "Solem BL-IP - ",
        "Solem BL-IP - AA:BB:CC:DD:EE",
        "Solem BL-IP",
        "not-a-mac",
        "AA:BB:CC:DD:EE:ZZ",
    ],
)
def test_normalize_controller_mac_rejects_invalid_strings(raw: str | None) -> None:
    """Anything that is not a complete MAC yields None."""
    assert normalize_controller_mac(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        170,
        0,
        b"AABBCCDDEEFF",
        b"AA:BB:CC:DD:EE:FF",
        ["AABBCCDDEEFF"],
        ("AABBCCDDEEFF",),
        {"mac": "AABBCCDDEEFF"},
        True,
    ],
)
def test_normalize_controller_mac_rejects_non_string_input(raw: object) -> None:
    """Non-string input returns None instead of raising."""
    assert normalize_controller_mac(raw) is None
