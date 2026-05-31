"""Config-flow behavior tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.config_flow import SolemConfigFlow
from custom_components.solem_blip.const import CONTROLLER_MAC_ADDRESS, NUM_STATIONS


@pytest.mark.asyncio
async def test_bluetooth_discovery_requires_confirmation_and_normalizes_mac(
    hass: HomeAssistant,
) -> None:
    """Bluetooth discovery opens a confirmation form without creating an entry."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()

    result = await flow.async_step_bluetooth(
        SimpleNamespace(address="aa:bb:cc:dd:ee:ff", name="Solem BL-IP")
    )

    flow.async_set_unique_id.assert_awaited_once_with("AA:BB:CC:DD:EE:FF")
    assert result["type"] == "form"
    assert result["step_id"] == "bluetooth_confirm"


@pytest.mark.asyncio
async def test_bluetooth_confirmation_validates_before_creation(
    hass: HomeAssistant,
) -> None:
    """Confirmed Bluetooth discovery creates an entry only after validation."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow._discovered_controller = "Solem BL-IP - AA:BB:CC:DD:EE:FF"
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(return_value={"title": "Solem BL-IP"}),
    ):
        result = await flow.async_step_bluetooth_confirm({NUM_STATIONS: 4})

    assert result == {"type": "create_entry"}
    flow.async_create_entry.assert_called_once_with(
        title="Solem BL-IP - AA:BB:CC:DD:EE:FF",
        data={
            CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
            NUM_STATIONS: 4,
        },
    )


@pytest.mark.asyncio
async def test_reconfigure_updates_station_count_without_changing_controller(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure keeps the physical controller and stable unique ID."""
    mock_config_entry.add_to_hass(hass)
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": mock_config_entry.entry_id}
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})

    result = await flow.async_step_reconfigure({NUM_STATIONS: 6})

    assert result == {"type": "abort"}
    flow.async_update_reload_and_abort.assert_called_once_with(
        mock_config_entry,
        unique_id=mock_config_entry.unique_id,
        data={
            **mock_config_entry.data,
            NUM_STATIONS: 6,
        },
        reason="reconfigure_successful",
    )
