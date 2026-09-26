"""Identification-name reads and HA identity/override preservation.

Mirrors the fork's tests (ThomasHFWright/solem-blip-ha PR #1,
tests/test_controller_name.py) adapted to this integration's
architecture: the BLE-side decode/wait tests live in the solem_blip_ble
library; here we cover the coordinator wiring and HA identity safety.
"""

from __future__ import annotations

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.const import (
    CONTROLLER_MAC_ADDRESS,
    CONTROLLER_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NUM_STATIONS,
)
from custom_components.solem_blip.controller_name import apply_controller_name
from custom_components.solem_blip.coordinator import SolemCoordinator

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
async def named_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONTROLLER_MAC_ADDRESS: f"Solem BL-IP - {ADDRESS}",
            NUM_STATIONS: 2,
        },
        options={
            DEFAULT_SCAN_INTERVAL: 60,
        },
        unique_id=ADDRESS,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def named_coordinator(hass, named_entry, mock_solem_client):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "custom_components.solem_blip.client_factory.StatelessSolemClient",
            lambda *args, **kwargs: mock_solem_client,
        )
        mp.setattr(
            "custom_components.solem_blip.bluetooth.async_get_connectable_device",
            lambda hass, address: None,
        )
        coordinator = SolemCoordinator(hass, named_entry)
    return coordinator


async def test_name_metadata_preserves_ha_identity_and_custom_labels(
    hass, named_coordinator, named_entry, mock_solem_client
):
    """Only integration-owned labels move; user renames are never touched."""
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=named_entry.entry_id,
        identifiers={(DOMAIN, ADDRESS)},
        connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)},
        name=ADDRESS,
    )
    # Title starts auto-generated (MAC) with no user rename.
    assert named_entry.title in (ADDRESS, "Solem BL-IP", "Mock Title")

    for name in ("Garden unit", "Second name"):
        mock_solem_client.get_firmware_version.return_value = {
            "major": 5,
            "minor": 1,
            "patch": 7,
            "raw_hex": "5.1.7",
            "controller_name": name,
        }
        # Simulate a fresh metadata read cycle each time: the firmware
        # read is retried, the name record is re-applied, and a second
        # application of the same name must not corrupt registry state.
        named_coordinator.firmware_version = None
        await named_coordinator._fetch_device_metadata()
        await named_coordinator._fetch_device_metadata()
        assert named_coordinator.firmware_version == "5.1.7"
        assert registry.async_get(device.id).name == name
        assert registry.async_get(device.id).name_by_user is None
        assert named_entry.data[CONTROLLER_NAME] == name
        assert named_coordinator.controller_name == name

    # A rebuilt coordinator (offline startup) restores the cached name.
    restored = SolemCoordinator(hass, named_entry)
    assert restored.controller_name == "Second name"


async def test_user_renamed_title_and_device_are_never_overwritten(
    hass, named_coordinator, named_entry, mock_solem_client
):
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=named_entry.entry_id,
        identifiers={(DOMAIN, ADDRESS)},
        connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)},
        name=ADDRESS,
    )
    registry.async_update_device(device.id, name_by_user="Custom label")
    hass.config_entries.async_update_entry(named_entry, title="My irrigation")

    mock_solem_client.get_firmware_version.return_value = {
        "major": 5,
        "minor": 1,
        "patch": 7,
        "raw_hex": "5.1.7",
        "controller_name": "Garden unit",
    }
    await named_coordinator._fetch_device_metadata()

    assert named_entry.title == "My irrigation"
    assert registry.async_get(device.id).name == "Garden unit"
    assert registry.async_get(device.id).name_by_user == "Custom label"
    assert named_entry.data[CONTROLLER_NAME] == "Garden unit"


async def test_previous_onboard_title_is_replaced(hass, named_coordinator, named_entry):
    """A title equal to the previous onboard name is still auto-generated."""
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=named_entry.entry_id,
        identifiers={(DOMAIN, ADDRESS)},
        name=ADDRESS,
    )
    hass.config_entries.async_update_entry(named_entry, title="Garden unit")
    named_coordinator.controller_name = "Garden unit"

    apply_controller_name(named_coordinator, "Renamed onboard")

    assert named_entry.title == "Renamed onboard"
    assert registry.async_get(device.id).name == "Renamed onboard"


async def test_missing_firmware_name_record_keeps_labels_unchanged(
    hass, named_coordinator, named_entry, mock_solem_client
):
    """Missing/malformed name record must not invalidate the firmware read."""
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=named_entry.entry_id,
        identifiers={(DOMAIN, ADDRESS)},
        name=ADDRESS,
    )
    mock_solem_client.get_firmware_version.return_value = {
        "major": 5,
        "minor": 1,
        "patch": 7,
        "raw_hex": "5.1.7",
    }
    await named_coordinator._fetch_device_metadata()

    assert named_coordinator.firmware_version == "5.1.7"
    assert named_coordinator.controller_name is None
    assert CONTROLLER_NAME not in named_entry.data
    assert registry.async_get(device.id).name == ADDRESS


async def test_firmware_read_failure_leaves_cached_name(
    hass, named_coordinator, named_entry, mock_solem_client
):
    apply_controller_name(named_coordinator, "Garden unit")
    mock_solem_client.get_firmware_version.side_effect = TimeoutError
    await named_coordinator._fetch_device_metadata()
    assert named_coordinator.controller_name == "Garden unit"
    assert named_entry.data[CONTROLLER_NAME] == "Garden unit"


async def test_missing_registry_and_entry_are_safe(hass, named_coordinator):
    """A missing device or detached entry must not raise."""
    apply_controller_name(named_coordinator, "Garden unit")
    assert named_coordinator.controller_name == "Garden unit"
    entry = named_coordinator.config_entry
    named_coordinator.config_entry = None
    apply_controller_name(named_coordinator, "Ignored")
    named_coordinator.config_entry = entry
    assert named_coordinator.controller_name == "Ignored"
