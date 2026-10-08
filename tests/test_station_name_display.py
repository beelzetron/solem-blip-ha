"""Station-name display propagation to valve, button, and sensor entities.

Regression tests for issue #136 follow-ups: a verified onboard rename
must reach every enabled station entity — including the valve and the
sprinkle button — immediately and across a config-entry reload, and
registry-disabled entities are expected to keep their frozen display
name until re-enabled (Home Assistant never recomputes their name).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip import util
from tests.conftest import SimpleSnapshot

NEW_NAMES = {1: "Test 1", 2: "Zone 2"}


def _friendly_name(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    name = state.attributes.get("friendly_name")
    return str(name) if name is not None else None


def _registry_entry(hass: HomeAssistant, domain: str, device_id: str) -> er.RegistryEntry:
    registry = er.async_get(hass)
    return registry.async_get_or_create(
        domain,
        "solem_blip",
        util.format_entity_unique_id("AA:BB:CC:DD:EE:FF", device_id),
    )


@pytest.mark.parametrize("language", ["en", "fr"])
async def test_rename_updates_valve_button_and_sensor_names(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client,
    enable_custom_integrations,
    language: str,
) -> None:
    """A verified rename reaches every enabled station entity, incl. reload."""
    hass.config.language = language
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entry = mock_config_entry
        coordinator = entry.runtime_data.coordinator

        snapshot = SimpleSnapshot(NEW_NAMES)
        with patch.object(
            coordinator.station_name_manager,
            "update",
            AsyncMock(return_value=snapshot),
        ):
            await coordinator.rename_station(1, "Test 1", "rev-1")

        await hass.async_block_till_done()

        valve_id = _registry_entry(
            hass, "valve", "AA:BB:CC:DD:EE:FF_irrigation_station_1_valve"
        ).entity_id
        button_id = _registry_entry(
            hass,
            "button",
            "AA:BB:CC:DD:EE:FF_irrigation_manual_start_station_1",
        ).entity_id
        status_id = _registry_entry(
            hass, "sensor", "AA:BB:CC:DD:EE:FF_irrigation_station_1_status"
        ).entity_id

        if language == "en":
            expected = {
                "valve": "Test 1",
                "button": "Sprinkle Test 1",
                "status": "Test 1 status",
            }
        else:
            expected = {
                "valve": "Test 1",
                "button": "Arroser Test 1",
                "status": "État de Test 1",
            }

        assert _friendly_name(hass, valve_id) == f"AA:BB:CC:DD:EE:FF {expected['valve']}"
        assert _friendly_name(hass, button_id) == f"AA:BB:CC:DD:EE:FF {expected['button']}"
        assert (
            _friendly_name(hass, status_id)
            == f"AA:BB:CC:DD:EE:FF {expected['status']}"
        )
        assert valve_id is not None
        assert (
            er.async_get(hass).async_get(valve_id).original_name == expected["valve"]
        )

        # A restart rebuilds entities from the registry: names must hold.
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert _friendly_name(hass, valve_id) == f"AA:BB:CC:DD:EE:FF {expected['valve']}"
        assert _friendly_name(hass, button_id) == f"AA:BB:CC:DD:EE:FF {expected['button']}"
        assert (
            er.async_get(hass).async_get(valve_id).original_name == expected["valve"]
        )


async def test_disabled_entities_keep_frozen_names_until_re_enabled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_solem_client,
    enable_custom_integrations,
) -> None:
    """Registry-disabled entities never recompute names; re-enabling does."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_solem_client,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        valve_entry = _registry_entry(
            hass, "valve", "AA:BB:CC:DD:EE:FF_irrigation_station_1_valve"
        )
        button_entry = _registry_entry(
            hass,
            "button",
            "AA:BB:CC:DD:EE:FF_irrigation_manual_start_station_1",
        )
        registry = er.async_get(hass)
        registry.async_update_entity(
            valve_entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        registry.async_update_entity(
            button_entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        await hass.async_block_till_done()
        await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data.coordinator
        snapshot = SimpleSnapshot(NEW_NAMES)
        with patch.object(
            coordinator.station_name_manager,
            "update",
            AsyncMock(return_value=snapshot),
        ):
            await coordinator.rename_station(1, "Test 1", "rev-1")
        await hass.async_block_till_done()

        # Disabled entities keep the pre-rename name even across restarts.
        registry = er.async_get(hass)
        assert (
            registry.async_get(valve_entry.entity_id).original_name == "Station 1"
        )
        assert (
            registry.async_get(button_entry.entity_id).original_name
            == "Sprinkle Station 1"
        )

        # Re-enabling (as the UI does) applies the new name immediately.
        registry.async_update_entity(valve_entry.entity_id, disabled_by=None)
        await hass.async_block_till_done()
        await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        assert (
            registry.async_get(valve_entry.entity_id).original_name == "Test 1"
        )
