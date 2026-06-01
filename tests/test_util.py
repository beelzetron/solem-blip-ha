"""Utility helper tests."""

from __future__ import annotations

from custom_components.solem_blip.util import (
    format_entity_unique_id,
    is_legacy_unique_id,
    normalize_entity_state,
)


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
