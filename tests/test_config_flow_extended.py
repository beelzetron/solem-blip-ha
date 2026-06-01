"""Extended config-flow coverage tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_SCAN_INTERVAL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.config_flow import (
    CannotConnect,
    CannotConnectSlots,
    SolemConfigFlow,
    SolemOptionsFlowHandler,
    validate_input,
)
from custom_components.solem_blip.const import (
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NUM_STATIONS,
    SOLEM_API_MOCK,
)
from solem_blip_ble import SolemConnectionError


@pytest.mark.asyncio
async def test_validate_input_requires_connectable_device(hass: HomeAssistant) -> None:
    """Validation fails when no connectable BLE device is available."""
    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=None,
    ):
        with pytest.raises(CannotConnect):
            await validate_input(
                hass,
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"},
            )


@pytest.mark.asyncio
async def test_validate_input_connects_successfully(hass: HomeAssistant) -> None:
    """Validation succeeds when BLE connect works."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock()
    mock_api.disconnect = AsyncMock()

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.config_flow.SolemClient",
        return_value=mock_api,
    ):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"},
        )

    assert result["title"] == "Solem BL-IP"
    mock_api.connect.assert_awaited_once()
    mock_api.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_validate_input_retries_busy_slots(hass: HomeAssistant) -> None:
    """Validation retries when BLE adapters are temporarily out of slots."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock(
        side_effect=[
            SolemConnectionError("No free connection slots"),
            None,
        ]
    )
    mock_api.disconnect = AsyncMock()

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.config_flow.SolemClient",
        return_value=mock_api,
    ), patch(
        "custom_components.solem_blip.config_flow.asyncio.sleep",
        new=AsyncMock(),
    ):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"},
        )

    assert result["title"] == "Solem BL-IP"
    assert mock_api.connect.await_count == 2


@pytest.mark.asyncio
async def test_validate_input_slots_with_discovery_proceeds(
    hass: HomeAssistant,
) -> None:
    """Validation proceeds when slots are busy but discovery still sees the controller."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock(
        side_effect=SolemConnectionError("No free connection slots")
    )
    mock_api.disconnect = AsyncMock()

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.config_flow.SolemClient",
        return_value=mock_api,
    ), patch(
        "custom_components.solem_blip.config_flow.async_is_device_discovered",
        return_value=True,
    ):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"},
        )

    assert result["title"] == "Solem BL-IP"


@pytest.mark.asyncio
async def test_validate_input_slots_without_discovery_raises(
    hass: HomeAssistant,
) -> None:
    """Validation raises CannotConnectSlots when discovery cannot see the controller."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock(
        side_effect=SolemConnectionError("No free connection slots")
    )
    mock_api.disconnect = AsyncMock()

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.config_flow.SolemClient",
        return_value=mock_api,
    ), patch(
        "custom_components.solem_blip.config_flow.async_is_device_discovered",
        return_value=False,
    ):
        with pytest.raises(CannotConnectSlots):
            await validate_input(
                hass,
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"},
            )


@pytest.mark.asyncio
async def test_validate_input_generic_connect_error(hass: HomeAssistant) -> None:
    """Validation raises CannotConnect for non-slot connection failures."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock(side_effect=SolemConnectionError("timeout"))
    mock_api.disconnect = AsyncMock()

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.config_flow.SolemClient",
        return_value=mock_api,
    ):
        with pytest.raises(CannotConnect):
            await validate_input(
                hass,
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"},
            )


@pytest.mark.asyncio
async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """User step shows a form when no input is provided."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {}

    with patch(
        "custom_components.solem_blip.config_flow.async_scan_devices",
        new=AsyncMock(return_value=[]),
    ):
        result = await flow.async_step_user()

    assert result["type"] == "form"
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """User step creates an entry after successful validation."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    user_input = {
        CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
        NUM_STATIONS: 3,
    }

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(return_value={"title": "Solem BL-IP"}),
    ):
        result = await flow.async_step_user(user_input)

    assert result == {"type": "create_entry"}
    flow.async_create_entry.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "error_key"),
    [
        (CannotConnectSlots(), "cannot_connect_slots"),
        (CannotConnect(), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_step_maps_validation_errors(
    hass: HomeAssistant, exc: Exception, error_key: str
) -> None:
    """User step maps validation failures to form errors."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {}

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(side_effect=exc),
    ), patch(
        "custom_components.solem_blip.config_flow.async_scan_devices",
        new=AsyncMock(return_value=[]),
    ):
        result = await flow.async_step_user(
            {
                CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
                NUM_STATIONS: 2,
            }
        )

    assert result["errors"]["base"] == error_key


@pytest.mark.asyncio
async def test_bluetooth_confirm_validation_errors(hass: HomeAssistant) -> None:
    """Bluetooth confirm maps validation failures to form errors."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow._discovered_controller = "Solem BL-IP - AA:BB:CC:DD:EE:FF"

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(side_effect=CannotConnect()),
    ):
        result = await flow.async_step_bluetooth_confirm({NUM_STATIONS: 2})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_bluetooth_confirm_slots_error(hass: HomeAssistant) -> None:
    """Bluetooth confirm maps slot exhaustion to a form error."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow._discovered_controller = "Solem BL-IP - AA:BB:CC:DD:EE:FF"

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(side_effect=CannotConnectSlots()),
    ):
        result = await flow.async_step_bluetooth_confirm({NUM_STATIONS: 2})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect_slots"


@pytest.mark.asyncio
async def test_bluetooth_confirm_unknown_error(hass: HomeAssistant) -> None:
    """Bluetooth confirm maps unexpected failures to the unknown error."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow._discovered_controller = "Solem BL-IP - AA:BB:CC:DD:EE:FF"

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await flow.async_step_bluetooth_confirm({NUM_STATIONS: 2})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_validate_input_raises_when_no_connect_attempts(
    hass: HomeAssistant,
) -> None:
    """Validation fails when no BLE connect attempts were made."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock()
    mock_api.disconnect = AsyncMock()

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.config_flow.SolemClient",
        return_value=mock_api,
    ), patch(
        "custom_components.solem_blip.config_flow.CONFIG_FLOW_CONNECT_RETRIES",
        0,
    ):
        with pytest.raises(CannotConnect):
            await validate_input(
                hass,
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"},
            )


@pytest.mark.asyncio
async def test_reconfigure_shows_form(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure shows a form before submission."""
    mock_config_entry.add_to_hass(hass)
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": mock_config_entry.entry_id}

    result = await flow.async_step_reconfigure()

    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"


@pytest.mark.asyncio
async def test_options_flow_updates_settings(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options flow stores updated polling and BLE settings."""
    mock_config_entry.add_to_hass(hass)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_init(
            {
                CONF_SCAN_INTERVAL: 120,
                BLUETOOTH_TIMEOUT: 45,
                SOLEM_API_MOCK: "true",
            }
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SCAN_INTERVAL] == 120


@pytest.mark.asyncio
async def test_options_flow_shows_form(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options flow shows the init form when no input is provided."""
    mock_config_entry.add_to_hass(hass)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_config_flow_options_factory(
    mock_config_entry: MockConfigEntry,
) -> None:
    """Config flow exposes the options handler factory."""
    handler = SolemConfigFlow.async_get_options_flow(mock_config_entry)
    assert isinstance(handler, SolemOptionsFlowHandler)


@pytest.mark.asyncio
async def test_bluetooth_step_aborts_duplicate(hass: HomeAssistant) -> None:
    """Bluetooth discovery aborts when the controller is already configured."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock(
        side_effect=Exception("already configured")
    )

    with pytest.raises(Exception, match="already configured"):
        await flow.async_step_bluetooth(
            SimpleNamespace(address="aa:bb:cc:dd:ee:ff", name="Solem BL-IP")
        )
