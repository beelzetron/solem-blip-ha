"""Config-entry migration for stable entity unique IDs."""

from __future__ import annotations

import logging
from collections import Counter

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .config_entry import MyConfigEntry
from .const import CONTROLLER_MAC_ADDRESS, DOMAIN, NUM_STATIONS, PROGRAM_LABELS
from .entity_identity import build_legacy_unique_id_map
from .util import is_legacy_unique_id

_LOGGER = logging.getLogger(__name__)

_REMOVED_PROGRAM_NAME_SUFFIXES = tuple(
    f"-program_{label.lower()}_name" for label in PROGRAM_LABELS
)


def _is_removed_program_name_entity(unique_id: str | None) -> bool:
    """Return True for unique IDs of the retired program name sensor."""
    if not unique_id:
        return False
    return unique_id.endswith(_REMOVED_PROGRAM_NAME_SUFFIXES)


async def async_remove_program_name_entities(
    hass: HomeAssistant, config_entry: MyConfigEntry
) -> None:
    """Remove program name sensors dropped in favor of titled program entities."""
    ent_reg = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        if not _is_removed_program_name_entity(entry.unique_id):
            continue
        _LOGGER.info("Removing retired program name entity %s", entry.entity_id)
        ent_reg.async_remove(entry.entity_id)


async def async_migrate_unique_ids(
    hass: HomeAssistant, config_entry: MyConfigEntry
) -> None:
    """Migrate counter-based unique IDs and remove orphaned registry rows."""
    mac = config_entry.data[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[1]
    num_stations = config_entry.data.get(NUM_STATIONS, 2)
    legacy_map = build_legacy_unique_id_map(mac, num_stations)

    ent_reg = er.async_get(hass)

    @callback
    def _migrate_entity(entry: er.RegistryEntry) -> dict[str, str] | None:
        if entry.config_entry_id != config_entry.entry_id:
            return None
        if not entry.unique_id or not entry.unique_id.startswith(f"{DOMAIN}-"):
            return None
        new_unique_id = legacy_map.get(entry.unique_id)
        if new_unique_id is None:
            return None
        return {"new_unique_id": new_unique_id}

    await er.async_migrate_entries(hass, config_entry.entry_id, _migrate_entity)

    await _async_remove_orphaned_entities(hass, config_entry, legacy_map)
    await async_remove_program_name_entities(hass, config_entry)


async def _async_remove_orphaned_entities(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    legacy_map: dict[str, str],
) -> None:
    """Remove legacy, restored, and duplicate registry entries."""
    ent_reg = er.async_get(hass)
    expected_new_ids = set(legacy_map.values())
    seen_new_ids: Counter[str] = Counter()

    for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        if not entry.unique_id or not entry.unique_id.startswith(f"{DOMAIN}-"):
            continue

        state = hass.states.get(entry.entity_id)
        is_restored = bool(
            state is not None and state.attributes.get("restored") is True
        )
        is_legacy = is_legacy_unique_id(entry.unique_id)
        is_duplicate = False

        if entry.unique_id in expected_new_ids:
            seen_new_ids[entry.unique_id] += 1
            if seen_new_ids[entry.unique_id] > 1:
                is_duplicate = True
                _LOGGER.warning(
                    "Removing duplicate %s (unique_id=%s)",
                    entry.entity_id,
                    entry.unique_id,
                )

        if (
            is_restored
            or is_legacy
            or is_duplicate
            or _is_removed_program_name_entity(entry.unique_id)
        ):
            _LOGGER.info(
                "Removing orphaned entity %s (restored=%s legacy=%s duplicate=%s)",
                entry.entity_id,
                is_restored,
                is_legacy,
                is_duplicate,
            )
            ent_reg.async_remove(entry.entity_id)
