"""Extended config-flow coverage tests."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
import voluptuous_serialize
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_SCAN_INTERVAL
import homeassistant.helpers.config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.config_flow import (
    CannotConnect,
    CannotConnectSlots,
    MENU_EDIT_PROGRAM,
    MENU_SETTINGS,
    SolemConfigFlow,
    SolemOptionsFlowHandler,
    validate_input,
)
from custom_components.solem_blip.config_entry import RuntimeData
from custom_components.solem_blip.const import (
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NUM_STATIONS,
    SOLEM_API_MOCK,
)
from solem_blip_ble import SolemConnectionError
from tests.conftest import MOCK_IRRIGATION_PROGRAMS


def _program_editor_input(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "name": "Vasi",
        "cycle": "periodic",
        "week_days": ["monday", "wednesday"],
        "period_start_date": date(2026, 6, 18),
        "period_length": 1,
        "synchro_day": 0,
        "water_budget": 100,
        "inter_station_delay": 0,
        "start_time_1": "06:30",
        "start_time_2": "",
        "start_time_3": "",
        "start_time_4": "",
        "start_time_5": "",
        "start_time_6": "",
        "start_time_7": "",
        "start_time_8": "",
        "station_1_duration": 0,
        "station_2_duration": 2,
    }
    data.update(overrides)
    return data


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
async def test_options_flow_shows_menu(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options flow shows the top-level options menu."""
    mock_config_entry.add_to_hass(hass)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_init()

    assert result["type"] == "menu"
    assert result["step_id"] == "init"
    assert result["menu_options"] == [MENU_SETTINGS, MENU_EDIT_PROGRAM]


@pytest.mark.parametrize(
    "translation_file",
    [
        Path("custom_components/solem_blip/strings.json"),
        Path("custom_components/solem_blip/translations/en.json"),
        Path("custom_components/solem_blip/translations/it.json"),
    ],
)
def test_options_flow_menu_translations_exist(
    translation_file: Path,
) -> None:
    """Options flow menu labels use the supported HA translation location."""
    translations = json.loads(translation_file.read_text())
    menu_options = translations["options"]["step"]["init"]["menu_options"]

    assert menu_options[MENU_SETTINGS]
    assert menu_options[MENU_EDIT_PROGRAM]


@pytest.mark.asyncio
async def test_options_flow_settings_shows_form(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options flow settings path shows the polling/BLE form."""
    mock_config_entry.add_to_hass(hass)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_settings()

    assert result["type"] == "form"
    assert result["step_id"] == "settings"


@pytest.mark.asyncio
async def test_options_flow_settings_updates_settings(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options flow settings path stores updated polling and BLE settings."""
    mock_config_entry.add_to_hass(hass)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_settings(
            {
                CONF_SCAN_INTERVAL: 90,
                BLUETOOTH_TIMEOUT: 35,
                SOLEM_API_MOCK: "false",
            }
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SCAN_INTERVAL] == 90


@pytest.mark.asyncio
async def test_options_flow_program_select_shows_form(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options flow can choose the on-device program to edit."""
    mock_config_entry.add_to_hass(hass)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_select()

    assert result["type"] == "form"
    assert result["step_id"] == "program_select"


def test_options_flow_program_select_uses_program_names() -> None:
    """Program selector labels include loaded on-device names."""
    coordinator = MagicMock()
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    handler = SolemOptionsFlowHandler()

    assert handler._program_select_options(coordinator) == [
        {"value": "1", "label": "Program A - Programma A"},
        {"value": "2", "label": "Program B - Programma B"},
        {"value": "3", "label": "Program C - Programma C"},
    ]


@pytest.mark.asyncio
async def test_options_flow_program_select_continues_to_editor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Selecting a program opens the editor form."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator, None)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_select({"program": "3"})

    assert result["type"] == "form"
    assert result["step_id"] == "program_edit"
    assert handler._selected_program_index == 2


@pytest.mark.asyncio
async def test_options_flow_program_edit_requires_loaded_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Program editor reports unloaded entries."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.runtime_data = None
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit()

    assert result["type"] == "form"
    assert result["errors"]["base"] == "not_loaded"


@pytest.mark.asyncio
async def test_options_flow_program_edit_writes_program(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Program editor writes through the coordinator."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator, None)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(_program_editor_input())

    assert result["type"] == "create_entry"
    coordinator.set_irrigation_program.assert_awaited_once()
    program_index, program = coordinator.set_irrigation_program.await_args.args
    assert program_index == 1
    assert program["name"] == "Vasi"
    assert program["cycle"] == 4
    assert program["week_days"] == 0x05
    assert program["period_start_date"] == date(2026, 6, 18)
    assert program["start_times"] == [390, None, None, None, None, None, None, None]
    assert program["station_durations"] == [0, 120]


def test_options_flow_program_edit_preserves_synchro_day_when_start_date_unchanged() -> None:
    """Program editor keeps the existing phase when the period start is unchanged."""
    handler = SolemOptionsFlowHandler()

    program = handler._program_from_options_input(
        _program_editor_input(
            period_start_date=date(2026, 6, 1),
            period_length=3,
            synchro_day=1,
        ),
        num_stations=2,
        current_program=MOCK_IRRIGATION_PROGRAMS[2],
    )

    assert program["synchro_day"] == 1


def test_options_flow_program_edit_derives_synchro_day_from_current_anchor() -> None:
    """Changing the desired start date shifts phase from the controller anchor."""
    handler = SolemOptionsFlowHandler()
    current_program = {
        **MOCK_IRRIGATION_PROGRAMS[2],
        "period_length": 3,
        "period_start_date": date(2026, 6, 27),
        "synchro_day": 0,
    }

    program = handler._program_from_options_input(
        _program_editor_input(
            period_start_date=date(2026, 6, 28),
            period_length=3,
            synchro_day=0,
        ),
        num_stations=2,
        current_program=current_program,
    )

    assert program["period_start_date"] == date(2026, 6, 28)
    assert program["synchro_day"] == 1


def test_options_flow_program_edit_resets_synchro_day_without_current_anchor() -> None:
    """Changing the period start uses zero phase when no read-back anchor exists."""
    handler = SolemOptionsFlowHandler()

    program = handler._program_from_options_input(
        _program_editor_input(
            period_start_date=date(2026, 6, 27),
            period_length=2,
            synchro_day=1,
        ),
        num_stations=2,
        current_program=None,
    )

    assert program["synchro_day"] == 0


@pytest.mark.asyncio
async def test_options_flow_program_edit_writes_named_station_fields(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Program editor accepts dynamic duration fields named after stations."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.station_names = {1: "Front lawn", 2: "Herbs"}
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator, None)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1
    user_input = _program_editor_input()
    del user_input["station_1_duration"]
    del user_input["station_2_duration"]
    user_input["Front lawn (station 1) duration (minutes)"] = 0
    user_input["Herbs (station 2) duration (minutes)"] = 2
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(user_input)

    assert result["type"] == "create_entry"
    _, program = coordinator.set_irrigation_program.await_args.args
    assert program["station_durations"] == [0, 120]


def test_options_flow_program_edit_defaults_show_duration_minutes() -> None:
    """Program editor exposes station durations in minutes."""
    handler = SolemOptionsFlowHandler()

    defaults = handler._program_defaults(
        MOCK_IRRIGATION_PROGRAMS[2],
        num_stations=4,
    )

    assert defaults["station_1_duration"] == 0
    assert defaults["station_2_duration"] == 25
    assert defaults["station_3_duration"] == 25
    assert defaults["station_4_duration"] == 0


def test_options_flow_program_edit_schema_serializes(
    mock_config_entry: MockConfigEntry,
) -> None:
    """Program editor schema is serializable by Home Assistant."""
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = voluptuous_serialize.convert(
            handler._program_schema(MOCK_IRRIGATION_PROGRAMS[1]),
            custom_serializer=cv.custom_serializer,
        )

    assert any(field["name"] == "station_2_duration" for field in serialized)


def test_options_flow_program_edit_schema_uses_station_names(
    mock_config_entry: MockConfigEntry,
) -> None:
    """Program editor schema exposes loaded station names in duration labels."""
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = voluptuous_serialize.convert(
            handler._program_schema(
                MOCK_IRRIGATION_PROGRAMS[2],
                station_names={1: "Front lawn", 2: "Herbs"},
            ),
            custom_serializer=cv.custom_serializer,
        )

    field_names = {field["name"] for field in serialized}
    assert "Front lawn (station 1) duration (minutes)" in field_names
    assert "Herbs (station 2) duration (minutes)" in field_names


@pytest.mark.asyncio
async def test_options_flow_program_edit_rejects_active_watering(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Program editor blocks writes while watering is active."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = True
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator, None)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(_program_editor_input())

    assert result["type"] == "form"
    assert result["errors"]["base"] == "set_program_while_watering"
    coordinator.set_irrigation_program.assert_not_awaited()


@pytest.mark.asyncio
async def test_options_flow_program_edit_rejects_invalid_time(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Program editor maps malformed start times to form errors."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator, None)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(
            _program_editor_input(start_time_1="25:00")
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_program"
    coordinator.set_irrigation_program.assert_not_awaited()


@pytest.mark.asyncio
async def test_options_flow_program_edit_reports_write_failure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Program editor reports coordinator write failures."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock(side_effect=RuntimeError("boom"))
    mock_config_entry.runtime_data = RuntimeData(coordinator, None)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(_program_editor_input())

    assert result["type"] == "form"
    assert result["errors"]["base"] == "set_program_failed"


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
