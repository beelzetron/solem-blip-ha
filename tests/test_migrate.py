"""Entity registry unique_id migration tests."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import DOMAIN
from custom_components.solem_blip.const import CONTROLLER_MAC_ADDRESS, NUM_STATIONS
from custom_components.solem_blip.entity_identity import (
    build_legacy_unique_id_map,
    iter_entity_identities,
)
from custom_components.solem_blip.migrate import (
    async_migrate_unique_ids,
    async_remove_program_name_entities,
)
from custom_components.solem_blip.util import format_entity_unique_id, is_legacy_unique_id

MAC = "AA:BB:CC:DD:EE:FF"


def test_legacy_map_maps_program_b_next_start() -> None:
    """Program B next-start counter slot migrates to the semantic unique_id."""
    mapping = build_legacy_unique_id_map(MAC, num_stations=2)
    program_b = next(
        identity
        for identity in iter_entity_identities(MAC, 2)
        if identity.device_id.endswith("_program_b_next_start")
    )
    assert mapping[program_b.legacy_unique_id] == program_b.unique_id
    assert program_b.unique_id.endswith("-program_b_next_start")


def test_is_legacy_unique_id_distinguishes_formats() -> None:
    """Legacy detector matches counter IDs only."""
    program_b = next(
        identity
        for identity in iter_entity_identities(MAC, 2)
        if identity.device_id.endswith("_program_b_next_start")
    )
    assert is_legacy_unique_id(program_b.legacy_unique_id) is True
    assert is_legacy_unique_id(program_b.unique_id) is False


@pytest.mark.asyncio
async def test_async_migrate_unique_ids_updates_registry(hass: HomeAssistant) -> None:
    """Migration rewrites legacy unique IDs in the entity registry."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONTROLLER_MAC_ADDRESS: f"Solem BL-IP - {MAC}",
            NUM_STATIONS: 2,
        },
        unique_id=MAC,
    )
    config_entry.add_to_hass(hass)

    program_b = next(
        identity
        for identity in iter_entity_identities(MAC, 2)
        if identity.device_id.endswith("_program_b_next_start")
    )
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        DOMAIN,
        "sensor",
        program_b.legacy_unique_id,
        config_entry=config_entry,
        suggested_object_id="program_b_next_start_test",
    )

    await async_migrate_unique_ids(hass, config_entry)

    entity_id = ent_reg.async_get_entity_id(DOMAIN, "sensor", program_b.unique_id)
    assert entity_id is not None
    assert ent_reg.async_get_entity_id(DOMAIN, "sensor", program_b.legacy_unique_id) is None


@pytest.mark.asyncio
async def test_async_remove_program_name_entities(hass: HomeAssistant) -> None:
    """Retired program name sensors are removed from the entity registry."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONTROLLER_MAC_ADDRESS: f"Solem BL-IP - {MAC}",
            NUM_STATIONS: 2,
        },
        unique_id=MAC,
    )
    config_entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        DOMAIN,
        "sensor",
        f"solem_blip-{format_mac(MAC)}-program_a_name",
        config_entry=config_entry,
        suggested_object_id="program_a_name_legacy",
    )

    await async_remove_program_name_entities(hass, config_entry)

    assert (
        ent_reg.async_get_entity_id(
            DOMAIN, "sensor", f"solem_blip-{format_mac(MAC)}-program_a_name"
        )
        is None
    )


@pytest.mark.asyncio
async def test_async_migrate_unique_ids_removes_restored_orphans(
    hass: HomeAssistant,
) -> None:
    """Restored unavailable entities for this config entry are removed."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONTROLLER_MAC_ADDRESS: f"Solem BL-IP - {MAC}",
            NUM_STATIONS: 2,
        },
        unique_id=MAC,
    )
    config_entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    orphan_entry = ent_reg.async_get_or_create(
        DOMAIN,
        "sensor",
        "solem_blip-orphan-schedule",
        config_entry=config_entry,
        suggested_object_id="orphan_schedule",
    )
    hass.states.async_set(
        orphan_entry.entity_id,
        "unavailable",
        {"restored": True},
    )

    await async_migrate_unique_ids(hass, config_entry)

    assert ent_reg.async_get_entity_id(DOMAIN, "sensor", "solem_blip-orphan-schedule") is None


def test_format_entity_unique_id_uses_formatted_mac() -> None:
    """Stable unique_id uses format_mac and device_id suffix."""
    device_id = f"{MAC}_irrigation_station_1_status"
    assert format_entity_unique_id(MAC, device_id) == (
        f"solem_blip-{format_mac(MAC)}-irrigation_station_1_status"
    )
