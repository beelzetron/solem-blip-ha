"""Entity metadata regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.solem_blip.entity_descriptions import SENSOR_DESCRIPTIONS
from custom_components.solem_blip.sensor import BatterySensor, StateSensor
from custom_components.solem_blip.util import format_entity_unique_id


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

    entity = StateSensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["STATE_SENSOR"]
    )

    assert entity.unique_id == format_entity_unique_id(
        coordinator.controller_mac_address,
        device["device_id"],
    )
    assert entity.unique_id.endswith("-irrigation_station_1_status")
    assert entity._attr_translation_key == "station_status"
    assert entity._attr_translation_placeholders == {"station_name": "Station 1"}


@pytest.mark.asyncio
async def test_battery_entity_has_no_translation_placeholders_attribute(
    coordinator,
) -> None:
    """Entities without placeholders must not set _attr_translation_placeholders."""
    device = next(
        item for item in coordinator.data if item["device_type"] == "BATTERY_SENSOR"
    )

    entity = BatterySensor(
        coordinator, device, "state", SENSOR_DESCRIPTIONS["BATTERY_SENSOR"]
    )

    assert entity.translation_key == "battery"
    assert not hasattr(entity, "_attr_translation_placeholders")


def test_english_and_italian_entity_translations_are_present() -> None:
    """Both bundled languages include translated station status names."""
    translations = Path(__file__).parents[1] / "custom_components" / "solem_blip" / "translations"
    english = json.loads((translations / "en.json").read_text())
    italian = json.loads((translations / "it.json").read_text())

    assert english["entity"]["sensor"]["station_status"]["name"] == "{station_name} status"
    assert italian["entity"]["sensor"]["station_status"]["name"] == "Stato {station_name}"
    assert english["entity"]["button"]["start_program"]["name"] == "Start {program_name}"
    assert italian["entity"]["button"]["start_program"]["name"] == "Avvia {program_name}"
    assert italian["entity"]["sensor"]["station_status"]["state"] == {
        "active": "Attiva",
        "inactive": "Inattiva",
        "unknown": "Sconosciuto",
    }
