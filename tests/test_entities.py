"""Entity metadata regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.solem_blip.sensor import StateSensor


@pytest.mark.asyncio
async def test_station_entity_keeps_unique_id_and_uses_translation_placeholder(
    coordinator,
) -> None:
    """Station entity identity stays stable while translated naming uses metadata."""
    device = next(
        item
        for item in coordinator.data
        if item["device_id"].endswith("_irrigation_station_1_status")
    )

    entity = StateSensor(coordinator, device, "state")

    assert entity.unique_id == (
        "solem_blip-AA:BB:CC:DD:EE:FF-"
        f"{device['device_uid']}-state"
    )
    assert entity._attr_translation_key == "station_status"
    assert entity._attr_translation_placeholders == {"station_name": "Station 1"}


def test_english_and_italian_entity_translations_are_present() -> None:
    """Both bundled languages include translated station status names."""
    translations = Path(__file__).parents[1] / "custom_components" / "solem_blip" / "translations"
    english = json.loads((translations / "en.json").read_text())
    italian = json.loads((translations / "it.json").read_text())

    assert english["entity"]["sensor"]["station_status"]["name"] == "{station_name} status"
    assert italian["entity"]["sensor"]["station_status"]["name"] == "Stato {station_name}"
