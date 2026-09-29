"""Extended config-flow coverage tests."""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
import voluptuous as vol
from probatio import to_field_list
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.config_entries import OptionsFlowWithReload
import homeassistant.helpers.config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.config_flow import (
    CONFIRM_DEGENERATE,
    CannotConnect,
    CannotConnectSlots,
    MENU_EDIT_PROGRAM,
    MENU_EDIT_STATION_NAMES,
    MENU_SETTINGS,
    SolemConfigFlow,
    SolemOptionsFlowHandler,
    _parse_program_input,
    validate_input,
)
from custom_components.solem_blip.config_entry import RuntimeData
from custom_components.solem_blip.const import (
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NUM_STATIONS,
    PERSISTENT_CONNECTION,
    SOLEM_API_MOCK,
)
from solem_blip_ble import IrrigationProgram, SolemConnectionError
from contextlib import contextmanager
from tests.conftest import MOCK_IRRIGATION_PROGRAMS


def _snapshot_api(
    *,
    connect_side_effect: list | None = None,
    snapshot: object | None = None,
    snapshot_side_effect: object = None,
) -> MagicMock:
    """Mock client: connect + a station-name snapshot read."""
    api = MagicMock()
    api.connect = AsyncMock(side_effect=connect_side_effect)
    if snapshot_side_effect is not None:
        api.get_station_name_snapshot = AsyncMock(side_effect=snapshot_side_effect)
    else:
        api.get_station_name_snapshot = AsyncMock(return_value=snapshot)
    return api


@contextmanager
def _patched_ble(mock_api: MagicMock):
    """Patch the config-flow BLE resolution to a mock client."""
    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_api,
    ):
        yield




def _entry_with_num_stations(num_stations: int) -> MockConfigEntry:
    """A MockConfigEntry whose data carries an explicit num_stations knob."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
            NUM_STATIONS: num_stations,
        },
        options={},
        unique_id="AA:BB:CC:DD:EE:FF",
    )


def _program_editor_input(**overrides: object) -> dict[str, object]:
    """Form input as the frontend submits it: periodic fields nested.

    ``period_length``/``period_start_date`` overrides are applied INSIDE
    the section (that is where the form renders them); any other override
    lands top-level.
    """
    periodic: dict[str, object] = {
        "period_start_date": date(2026, 6, 18),
        "period_length": 1,
    }
    for key in ("period_start_date", "period_length"):
        if key in overrides:
            periodic[key] = overrides.pop(key)
    data: dict[str, object] = {
        "name": "Vasi",
        "cycle": "periodic",
        "week_days": ["monday", "wednesday"],
        "periodic": periodic,
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
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
            )


@pytest.mark.asyncio
async def test_validate_input_connects_successfully(hass: HomeAssistant) -> None:
    """Validation succeeds when BLE connect works."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock()
    mock_api.get_station_name_snapshot = AsyncMock(
        return_value=SimpleNamespace(station_count=4)
    )

    with _patched_ble(mock_api):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
        )

    assert result["title"] == "Solem BL-IP"
    mock_api.connect.assert_awaited_once()


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

    with _patched_ble(mock_api), patch(
        "custom_components.solem_blip.config_flow.asyncio.sleep",
        new=AsyncMock(),
    ):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
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

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_api,
    ), patch(
        "custom_components.solem_blip.config_flow.async_is_device_discovered",
        return_value=True,
    ):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
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

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_api,
    ), patch(
        "custom_components.solem_blip.config_flow.async_is_device_discovered",
        return_value=False,
    ):
        with pytest.raises(CannotConnectSlots):
            await validate_input(
                hass,
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
            )


@pytest.mark.asyncio
async def test_validate_input_generic_connect_error(hass: HomeAssistant) -> None:
    """Validation raises CannotConnect for non-slot connection failures."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock(side_effect=SolemConnectionError("timeout"))

    with _patched_ble(mock_api):
        with pytest.raises(CannotConnect):
            await validate_input(
                hass,
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
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
        result = await flow.async_step_bluetooth_confirm({})

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
        result = await flow.async_step_bluetooth_confirm({})

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
        result = await flow.async_step_bluetooth_confirm({})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unknown"


@pytest.mark.asyncio
async def test_validate_input_derives_station_count_from_snapshot(
    hass: HomeAssistant,
) -> None:
    """A successful snapshot read replaces the configured count with the derived one."""
    mock_api = _snapshot_api(snapshot=SimpleNamespace(station_count=6))

    with _patched_ble(mock_api):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
        )

    assert result["title"] == "Solem BL-IP"
    assert result["num_stations"] == 6
    mock_api.get_station_name_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_validate_input_station_count_clamped_to_max(
    hass: HomeAssistant,
) -> None:
    """A derived count above MAX is clamped to MAX."""
    mock_api = _snapshot_api(snapshot=SimpleNamespace(station_count=99))

    with _patched_ble(mock_api):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
        )

    assert result["num_stations"] == 8


@pytest.mark.asyncio
async def test_validate_input_snapshot_failure_falls_back_to_configured(
    hass: HomeAssistant,
) -> None:
    """A failed snapshot read keeps the configured count; connect still succeeds."""
    mock_api = _snapshot_api(
        snapshot_side_effect=SolemConnectionError("link drop")
    )

    with _patched_ble(mock_api):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 3},
        )

    assert result["title"] == "Solem BL-IP"
    assert result["num_stations"] == 3


@pytest.mark.asyncio
async def test_validate_input_snapshot_without_count_falls_back(
    hass: HomeAssistant,
) -> None:
    """A client that does not expose station_count (mock mode) keeps the configured count."""
    mock_api = _snapshot_api(snapshot=SimpleNamespace(station_count=None))

    with _patched_ble(mock_api):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
        )

    assert result["num_stations"] == 2


@pytest.mark.asyncio
async def test_user_step_end_to_end_creates_entry_with_derived_count(
    hass: HomeAssistant,
) -> None:
    """User step through the REAL validate_input (no mock): blocker regression.

    The form no longer renders num_stations, so the submitted user_input
    lacks the key; async_step_user must inject the floor before calling
    validate_input, and the entry data must carry the device-derived count.
    """
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    mock_api = _snapshot_api(snapshot=SimpleNamespace(station_count=6))

    with _patched_ble(mock_api):
        result = await flow.async_step_user(
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF"}
        )

    assert result == {"type": "create_entry"}
    flow.async_create_entry.assert_called_once_with(
        title="Solem BL-IP - AA:BB:CC:DD:EE:FF",
        data={
            CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF",
            NUM_STATIONS: 6,
        },
    )


@pytest.mark.asyncio
async def test_derive_station_count_retries_once_after_link_release(
    hass: HomeAssistant,
) -> None:
    """The snapshot read retries once: the connect probe just released the link."""
    mock_api = _snapshot_api(
        snapshot_side_effect=[
            SolemConnectionError("link dropped"),
            SimpleNamespace(station_count=5),
        ]
    )

    with _patched_ble(mock_api), patch(
        "custom_components.solem_blip.config_flow.asyncio.sleep",
        new=AsyncMock(),
    ):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
        )

    assert result["num_stations"] == 5
    assert mock_api.get_station_name_snapshot.await_count == 2


@pytest.mark.asyncio
async def test_derive_station_count_never_shrinks_configured_floor(
    hass: HomeAssistant,
) -> None:
    """A derived count below the configured floor is adopted upward-only."""
    mock_api = _snapshot_api(snapshot=SimpleNamespace(station_count=1))

    with _patched_ble(mock_api):
        result = await validate_input(
            hass,
            {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 4},
        )

    assert result["num_stations"] == 4


@pytest.mark.asyncio
async def test_validate_input_raises_when_no_connect_attempts(
    hass: HomeAssistant,
) -> None:
    """Validation fails when no BLE connect attempts were made."""
    mock_api = MagicMock()
    mock_api.connect = AsyncMock()
    mock_api.get_station_name_snapshot = AsyncMock(
        return_value=SimpleNamespace(station_count=4)
    )

    with patch(
        "custom_components.solem_blip.config_flow.async_get_connectable_device",
        return_value=MagicMock(),
    ), patch(
        "custom_components.solem_blip.client_factory.StatelessSolemClient",
        return_value=mock_api,
    ), patch(
        "custom_components.solem_blip.config_flow.CONFIG_FLOW_CONNECT_RETRIES",
        0,
    ):
        with pytest.raises(CannotConnect):
            await validate_input(
                hass,
                {CONTROLLER_MAC_ADDRESS: "Solem BL-IP - AA:BB:CC:DD:EE:FF", NUM_STATIONS: 2},
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
    assert result["menu_options"] == [
        MENU_SETTINGS,
        MENU_EDIT_PROGRAM,
        MENU_EDIT_STATION_NAMES,
    ]


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
    assert menu_options[MENU_EDIT_STATION_NAMES]


def test_options_flow_uses_automatic_reload() -> None:
    """Options flow relies on HA automatic reload, not a manual update listener."""
    assert issubclass(SolemOptionsFlowHandler, OptionsFlowWithReload)


@pytest.mark.parametrize(
    "translation_file",
    [
        Path("custom_components/solem_blip/translations/en.json"),
        Path("custom_components/solem_blip/translations/it.json"),
        Path("custom_components/solem_blip/translations/fr.json"),
    ],
)
def test_options_flow_settings_translations_cover_strings(
    translation_file: Path,
) -> None:
    """Every options settings key in strings.json exists in en/it/fr translations."""
    strings = json.loads(
        Path("custom_components/solem_blip/strings.json").read_text()
    )
    translations = json.loads(translation_file.read_text())
    expected = set(strings["options"]["step"]["settings"]["data"])
    actual = set(translations["options"]["step"]["settings"]["data"])

    assert expected <= actual


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
async def test_options_flow_settings_persists_persistent_connection(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Options flow settings path shows and persists persistent_connection."""
    mock_config_entry.add_to_hass(hass)
    handler = SolemOptionsFlowHandler()
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        shown = await handler.async_step_settings()
        assert PERSISTENT_CONNECTION in shown["data_schema"].schema

        result = await handler.async_step_settings(
            {
                CONF_SCAN_INTERVAL: 90,
                BLUETOOTH_TIMEOUT: 35,
                SOLEM_API_MOCK: "false",
                PERSISTENT_CONNECTION: True,
            }
        )

    assert result["type"] == "create_entry"
    assert result["data"][PERSISTENT_CONNECTION] is True


@pytest.mark.asyncio
async def test_options_flow_settings_persistent_connection_default_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """persistent_connection defaults to False when not configured."""
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
    marker = next(
        key
        for key in result["data_schema"].schema
        if getattr(key, "schema", None) == PERSISTENT_CONNECTION
    )
    assert marker.default() is False


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
    mock_config_entry.runtime_data = RuntimeData(coordinator)
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
    mock_config_entry.runtime_data = RuntimeData(coordinator)
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
    """Program editor writes duration values read from the static keys."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.station_names = {1: "Front lawn", 2: "Herbs"}
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1
    user_input = _program_editor_input()
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


@pytest.mark.asyncio
async def test_program_edit_accepts_time_object_start(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Native time picker submits datetime.time; it round-trips to minutes."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.station_names = {1: "Front lawn", 2: "Herbs"}
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1
    user_input = _program_editor_input(start_time_1=time(6, 30))
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(user_input)

    assert result["type"] == "create_entry"
    _, program = coordinator.set_irrigation_program.await_args.args
    assert program["start_times"][0] == 390


@pytest.mark.asyncio
async def test_program_edit_midnight_time_object_is_not_disabled(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Midnight (time(0, 0)) is a valid start, not treated as falsy/empty."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.station_names = {1: "Front lawn", 2: "Herbs"}
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1
    user_input = _program_editor_input(start_time_1=time(0, 0))
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(user_input)

    assert result["type"] == "create_entry"
    _, program = coordinator.set_irrigation_program.await_args.args
    assert program["start_times"][0] == 0


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
        serialized = to_field_list(
            handler._program_schema(MOCK_IRRIGATION_PROGRAMS[1]),
            custom_serializer=cv.custom_serializer,
        )

    assert any(field["name"] == "station_2_duration" for field in serialized)

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized_a = to_field_list(
            handler._program_schema(MOCK_IRRIGATION_PROGRAMS[0]),
            custom_serializer=cv.custom_serializer,
        )

    start_field = next(
        field for field in serialized_a if field["name"] == "start_time_1"
    )
    assert start_field["default"] == "17:40"


def test_options_flow_program_edit_schema_uses_station_names(
    mock_config_entry: MockConfigEntry,
) -> None:
    """Duration field keys are static so strings.json labels translate.

    Dynamic keys (``<Name> (station N) duration (minutes)``) match no
    strings.json entry and render raw; the static ``station_N_duration``
    keys exist in all four locales (issue #129 field testing).
    """
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = to_field_list(
            handler._program_schema(
                MOCK_IRRIGATION_PROGRAMS[2],
                station_names={1: "Front lawn", 2: "Herbs"},
            ),
            custom_serializer=cv.custom_serializer,
        )

    field_names = {field["name"] for field in serialized}
    assert "station_1_duration" in field_names
    assert "station_2_duration" in field_names
    assert not any("(station" in name for name in field_names)


def test_program_schema_sizes_from_coordinator_width_not_config_knob(
    mock_config_entry: MockConfigEntry,
) -> None:
    """D1 (issue #122): after growth the editor renders the adopted width.

    The configured num_stations stays 2 but the coordinator adopted a
    device-derived width of 7: the schema must expose 7 duration fields
    (sized from coordinator.num_stations), not 2.
    """
    coordinator = MagicMock()
    coordinator.num_stations = 7
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        schema = handler._program_schema(MOCK_IRRIGATION_PROGRAMS[1])

    keys = {str(key) for key in schema.schema}
    assert sum(1 for key in keys if key.startswith("station_")) == 7
    for station in range(1, 8):
        assert f"station_{station}_duration" in keys


def test_program_schema_falls_back_to_entry_data_without_coordinator(
    mock_config_entry: MockConfigEntry,
) -> None:
    """D1 fallback: with no loaded coordinator the entry-data knob sizes it."""
    mock_config_entry = _entry_with_num_stations(3)
    mock_config_entry.runtime_data = None
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        schema = handler._program_schema(MOCK_IRRIGATION_PROGRAMS[1])

    keys = {str(key) for key in schema.schema}
    assert sum(1 for key in keys if key.startswith("station_")) == 3


@pytest.mark.asyncio
async def test_program_edit_validates_after_width_growth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """D1 regression: growth to 7 makes program edit validate AND write.

    Before the fix the form rendered 2 duration fields (config knob) while
    submission validated 7 (coordinator width) -> KeyError -> generic
    set_program_failed on every edit.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 7
    coordinator.station_names = {i: f"Zone {i}" for i in range(1, 8)}
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1

    user_input = _program_editor_input()
    # Only the configured two exist in a form rendered pre-growth; supply
    # the adopted width's fields (as the form now renders them).
    for station in range(3, 8):
        user_input[f"station_{station}_duration"] = 0

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(user_input)

    assert result["type"] == "create_entry"
    _, program = coordinator.set_irrigation_program.await_args.args
    assert program["station_durations"] == [0, 120, 0, 0, 0, 0, 0]


@pytest.mark.asyncio
async def test_program_edit_form_renders_grown_width(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The program edit form renders one duration field per adopted station."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 7
    coordinator.station_names = {}
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(None)

    assert result["type"] == "form"
    assert result["step_id"] == "program_edit"
    field_names = {
        str(key.schema) for key in result["data_schema"].schema
    }
    assert sum(1 for name in field_names if name.startswith("station_")) == 7


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
    mock_config_entry.runtime_data = RuntimeData(coordinator)
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
    mock_config_entry.runtime_data = RuntimeData(coordinator)
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
    mock_config_entry.runtime_data = RuntimeData(coordinator)
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


def test_program_schema_start_times_use_time_selector(
    mock_config_entry: MockConfigEntry,
) -> None:
    """Program start slots render as HA native time selectors (issue #129)."""
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = to_field_list(
            handler._program_schema(MOCK_IRRIGATION_PROGRAMS[1]),
            custom_serializer=cv.custom_serializer,
        )

    start_fields = [
        field for field in serialized if field["name"].startswith("start_time_")
    ]
    assert len(start_fields) == 8
    for field in start_fields:
        assert field["selector"] == {"time": {}}


def _degenerate_editor_input(**overrides: object) -> dict[str, object]:
    """Weekly program with no weekday selected but healthy durations."""
    return _program_editor_input(
        cycle="custom",
        week_days=[],
        **overrides,
    )


def _no_starts_editor_input(**overrides: object) -> dict[str, object]:
    """Healthy weekly program with every start slot cleared."""
    starts = {f"start_time_{i}": "" for i in range(1, 9)}
    return _program_editor_input(cycle="custom", **starts, **overrides)


def _loaded_editor_handler(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    *,
    station_names: dict[int, str] | None = None,
) -> tuple[SolemOptionsFlowHandler, MagicMock]:
    """A program editor wired to a loaded, idle coordinator."""
    mock_config_entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.num_stations = 2
    coordinator.station_names = station_names or {}
    coordinator.irrigation_programs = dict(MOCK_IRRIGATION_PROGRAMS)
    coordinator._irrigation_active = False
    coordinator._is_watering = False
    coordinator.set_irrigation_program = AsyncMock()
    mock_config_entry.runtime_data = RuntimeData(coordinator)
    handler = SolemOptionsFlowHandler()
    handler._selected_program_index = 1
    return handler, coordinator


@pytest.mark.asyncio
async def test_program_edit_week_days_zero_warns_and_does_not_write(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A weekly program with no day selected shows the preview-only confirm step."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(_degenerate_editor_input())

    assert result["type"] == "form"
    assert result["step_id"] == "program_degenerate_confirm"
    assert "base" not in (result["errors"] or {})
    coordinator.set_irrigation_program.assert_not_awaited()
    # Preview-only: the confirm step carries the single boolean and nothing else.
    field_names = {str(key.schema) for key in result["data_schema"].schema}
    assert field_names == {"confirm_degenerate"}
    placeholders = result["description_placeholders"]
    assert "never start" in placeholders["warning"]
    assert placeholders.get("preview")


@pytest.mark.asyncio
async def test_program_edit_no_start_times_warns_and_does_not_write(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A program with all start slots cleared shows the preview-only confirm step."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(_no_starts_editor_input())

    assert result["type"] == "form"
    assert result["step_id"] == "program_degenerate_confirm"
    assert "base" not in (result["errors"] or {})
    coordinator.set_irrigation_program.assert_not_awaited()
    field_names = {str(key.schema) for key in result["data_schema"].schema}
    assert field_names == {"confirm_degenerate"}
    placeholders = result["description_placeholders"]
    assert "start times" in placeholders["warning"]
    assert placeholders.get("preview")


@pytest.mark.asyncio
async def test_program_edit_no_start_times_confirm_resubmit_writes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Confirming the degenerate warning proceeds to the write."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        warned = await handler.async_step_program_edit(_no_starts_editor_input())
        assert warned["step_id"] == "program_degenerate_confirm"
        result = await handler.async_step_program_degenerate_confirm(
            {CONFIRM_DEGENERATE: True}
        )

    assert result["type"] == "create_entry"
    coordinator.set_irrigation_program.assert_awaited_once()


@pytest.mark.asyncio
async def test_program_edit_degenerate_confirm_resubmit_writes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Confirming the degenerate warning proceeds to the write."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        warned = await handler.async_step_program_edit(_degenerate_editor_input())
        assert warned["step_id"] == "program_degenerate_confirm"
        result = await handler.async_step_program_degenerate_confirm(
            {CONFIRM_DEGENERATE: True}
        )

    assert result["type"] == "create_entry"
    coordinator.set_irrigation_program.assert_awaited_once()


@pytest.mark.asyncio
async def test_program_edit_degenerate_declined_returns_to_fresh_editor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Declining the degenerate warning returns to a fresh program editor."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        warned = await handler.async_step_program_edit(_degenerate_editor_input())
        assert warned["step_id"] == "program_degenerate_confirm"
        result = await handler.async_step_program_degenerate_confirm(
            {CONFIRM_DEGENERATE: False}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "program_edit"
    coordinator.set_irrigation_program.assert_not_awaited()
    # Pending state cleared: the fresh editor shows the stored program.
    assert handler._pending_program is None
    defaults = _schema_defaults(result["data_schema"])
    assert defaults["name"] == "Programma B"


@pytest.mark.asyncio
async def test_program_edit_fresh_render_clears_pending_state(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A fresh (menu-entry) editor render clears any pending confirmation."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        warned = await handler.async_step_program_edit(_degenerate_editor_input())
        assert warned["step_id"] == "program_degenerate_confirm"
        assert handler._pending_program is not None
        await handler.async_step_program_edit(None)

    assert handler._pending_program is None
    assert handler._pending_program_index is None


@pytest.mark.asyncio
async def test_program_edit_healthy_input_writes_immediately(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Healthy configs write on first submit - no confirm round-trip."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(_program_editor_input())

    assert result["type"] == "create_entry"
    coordinator.set_irrigation_program.assert_awaited_once()


@pytest.mark.asyncio
async def test_program_edit_renders_preview_placeholder(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Validation errors still render a schedule preview from parseable input."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(
            _program_editor_input(station_1_duration=-5)
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_program"
    coordinator.set_irrigation_program.assert_not_awaited()
    preview = result["description_placeholders"]["preview"]
    assert preview
    assert "06:30" in preview


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


# --- Schedule presets: catalog + applier (issue #129, Task 1) ---


def _schema_defaults(schema: vol.Schema) -> dict[str, object]:
    """Resolve a rendered schema's defaults (HA wraps them in factories).

    Expandable sections are flattened: inner defaults surface under their
    own names prefixed by ``<section>.``.
    """
    def marker_default(key: object) -> object:
        default = getattr(key, "default", None)
        return default() if callable(default) else default

    defaults: dict[str, object] = {}
    for key, validator in schema.schema.items():
        inner = getattr(validator, "schema", None)
        if isinstance(inner, vol.Schema):  # section: descend into it
            section_default = marker_default(key) or {}
            for inner_key in inner.schema:
                inner_default = marker_default(inner_key)
                if inner_default is None and str(inner_key.schema) in section_default:
                    inner_default = section_default[str(inner_key.schema)]
                defaults[f"{key.schema}.{inner_key.schema}"] = inner_default
            continue
        defaults[str(key.schema)] = marker_default(key)
    return defaults


def test_apply_preset_every_day() -> None:
    """every_day restores native weekly semantics (issue #129)."""
    program = SolemOptionsFlowHandler._apply_preset("every_day", _program_editor_input())

    assert program["cycle"] == 0 and program["week_days"] == 0x7F
    assert program["period_length"] == 1 and program["synchro_day"] == 0


def test_apply_preset_even_and_odd() -> None:
    """even_days/odd_days map to the native parity cycles."""
    even = SolemOptionsFlowHandler._apply_preset("even_days", _program_editor_input())
    assert even["cycle"] == 1
    odd = SolemOptionsFlowHandler._apply_preset("odd_days", _program_editor_input())
    assert odd["cycle"] == 2


def test_apply_preset_renormalizes_synchro_day_to_new_period() -> None:
    """An anchored preset renormalizes the stored phase into its new period."""
    previous: IrrigationProgram = {
        "name": "Vasi",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 4,
        "week_days": 0x05,
        "period_length": 5,
        "synchro_day": 5,
        "period_start_date": date(2026, 6, 18),
        "start_times": [1060] + [None] * 7,
        "station_durations": [0, 2],
    }
    inp = _program_editor_input(
        period_start_date=date(2026, 6, 18), period_length=5
    )

    program = SolemOptionsFlowHandler._apply_preset(
        "every_3_days", inp, current_program=previous
    )

    assert program["period_length"] == 3
    assert program["synchro_day"] == 5 % 3


def test_apply_preset_anchored_periodic() -> None:
    """Anchored presets set the periodic cycle and keep the picked anchor."""
    inp = _program_editor_input()
    program = SolemOptionsFlowHandler._apply_preset("every_3_days", inp)

    assert program["cycle"] == 4 and program["period_length"] == 3
    assert program["period_start_date"] == inp["periodic"]["period_start_date"]


def test_apply_preset_none_returns_input_unchanged() -> None:
    """none is a passthrough: the parsed program carries no preset mutation."""
    inp = _program_editor_input()
    program = SolemOptionsFlowHandler._apply_preset("none", inp)
    expected = SolemOptionsFlowHandler()._program_from_options_input(
        inp, num_stations=2
    )

    assert program == expected
    assert program["cycle"] == 4  # parsed from the input's "periodic", untouched


def test_apply_preset_uses_named_station_fields() -> None:
    """The applier sizes stations from static duration field keys too."""
    inp = _program_editor_input()
    del inp["station_1_duration"]
    del inp["station_2_duration"]
    inp["station_1_duration"] = 1
    inp["station_2_duration"] = 2

    program = SolemOptionsFlowHandler._apply_preset(
        "every_2_days",
        inp,
        station_names={1: "Front lawn", 2: "Herbs"},
    )

    assert program["station_durations"] == [60, 120]


# --- Schedule presets: form field + preview-confirm wiring (issue #129, Task 2) ---


def test_program_schema_has_preset_field_first(
    mock_config_entry: MockConfigEntry,
) -> None:
    """The preset dropdown is the first field with exactly the 7 options."""
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = to_field_list(
            handler._program_schema(MOCK_IRRIGATION_PROGRAMS[1]),
            custom_serializer=cv.custom_serializer,
        )

    names = [field["name"] for field in serialized]
    assert names[0] == "schedule_preset"
    select = serialized[0]["selector"]["select"]
    assert select["options"] == [
        "none",
        "every_day",
        "even_days",
        "odd_days",
        "every_2_days",
        "every_3_days",
        "every_4_days",
    ]
    assert select["mode"] == "dropdown"
    assert select["translation_key"] == "schedule_preset_selector"
    assert names.index("schedule_preset") < names.index("cycle")


def test_program_schema_orders_advanced_fields_last(
    mock_config_entry: MockConfigEntry,
) -> None:
    """Periodic fields live in a collapsed 'periodic' section, rendered last."""
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = to_field_list(
            handler._program_schema(MOCK_IRRIGATION_PROGRAMS[1]),
            custom_serializer=cv.custom_serializer,
        )

    names = [field["name"] for field in serialized]
    assert "synchro_day" not in names
    assert names[-1] == "periodic"
    section_field = serialized[-1]
    assert section_field["type"] == "expandable"
    assert section_field["expanded"] is False
    inner_names = [field["name"] for field in section_field["schema"]]
    assert inner_names == ["period_start_date", "period_length"]
    last_station_duration = max(
        i for i, name in enumerate(names) if name.endswith("_duration")
    )
    assert last_station_duration < names.index("periodic")


def test_program_schema_periodic_section_defaults_are_nested(
    mock_config_entry: MockConfigEntry,
) -> None:
    """The section renders the stored program's periodic values as defaults."""
    handler = SolemOptionsFlowHandler()

    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        serialized = to_field_list(
            handler._program_schema(
                {
                    **MOCK_IRRIGATION_PROGRAMS[1],
                    "period_length": 5,
                    "period_start_date": date(2026, 3, 1),
                }
            ),
            custom_serializer=cv.custom_serializer,
        )

    section_field = next(
        field for field in serialized if field["name"] == "periodic"
    )
    defaults = {field["name"]: field["default"] for field in section_field["schema"]}
    assert defaults["period_length"] == 5
    # probatio may keep the date default raw or serialize it to ISO.
    assert str(defaults["period_start_date"]) == "2026-03-01"


@pytest.mark.asyncio
async def test_program_edit_preset_submit_shows_apply_confirm(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Submitting a preset shows the preview-only confirm step, no write."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    user_input = _program_editor_input(schedule_preset="every_3_days")
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "program_apply_confirm"
    assert "base" not in (result["errors"] or {})
    coordinator.set_irrigation_program.assert_not_awaited()
    # Preview-only: the confirm step carries the single boolean and nothing else.
    field_names = {str(key.schema) for key in result["data_schema"].schema}
    assert field_names == {"confirm_apply"}
    placeholders = result["description_placeholders"]
    assert placeholders.get("preview")
    assert placeholders.get("warning") == ""
    # The pending program carries the applied encoding (period_length 3).
    assert handler._pending_program is not None
    assert handler._pending_program["period_length"] == 3
    assert handler._pending_program_index == 1


@pytest.mark.asyncio
async def test_program_edit_preset_confirmed_second_submit_writes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Confirming the preview writes the pending (applied) program."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    user_input = _program_editor_input(schedule_preset="every_3_days")
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        warned = await handler.async_step_program_edit(user_input)
        assert warned["step_id"] == "program_apply_confirm"
        result = await handler.async_step_program_apply_confirm(
            {"confirm_apply": True}
        )

    assert result["type"] == "create_entry"
    coordinator.set_irrigation_program.assert_awaited_once()
    _, program = coordinator.set_irrigation_program.await_args.args
    # The pending program (applied preset encoding) is what was written.
    assert program["period_length"] == 3
    assert program["cycle"] == 4


@pytest.mark.asyncio
async def test_program_edit_preset_declined_returns_to_fresh_editor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Declining the preview returns to a fresh program editor, no write."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        warned = await handler.async_step_program_edit(
            _program_editor_input(schedule_preset="every_3_days")
        )
        assert warned["step_id"] == "program_apply_confirm"
        result = await handler.async_step_program_apply_confirm(
            {"confirm_apply": False}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "program_edit"
    coordinator.set_irrigation_program.assert_not_awaited()
    assert handler._pending_program is None
    # Fresh editor: the preset default is back to "none" and the stored
    # program's values render as defaults.
    defaults = _schema_defaults(result["data_schema"])
    assert defaults["schedule_preset"] == "none"
    assert defaults["name"] == "Programma B"


@pytest.mark.asyncio
async def test_program_edit_preset_none_writes_immediately(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A healthy submit with preset=none writes with no round-trip."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(
            _program_editor_input(schedule_preset="none")
        )

    assert result["type"] == "create_entry"
    coordinator.set_irrigation_program.assert_awaited_once()


@pytest.mark.asyncio
async def test_program_edit_preset_apply_degenerate_warns_without_write(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An applied preset whose schedule is degenerate warns instead of writing."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    user_input = _program_editor_input(
        schedule_preset="every_3_days",
        station_1_duration=0,
        station_2_duration=0,
    )
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        result = await handler.async_step_program_edit(user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "program_degenerate_confirm"
    assert "base" not in (result["errors"] or {})
    coordinator.set_irrigation_program.assert_not_awaited()
    field_names = {str(key.schema) for key in result["data_schema"].schema}
    assert field_names == {"confirm_degenerate"}
    assert result["description_placeholders"].get("warning")
    # The pending program carries the APPLIED periodic encoding.
    assert handler._pending_program is not None
    assert handler._pending_program["period_length"] == 3


@pytest.mark.asyncio
async def test_program_edit_preset_degenerate_confirm_writes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Confirming a degenerate preset-applied schedule writes it anyway."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    user_input = _program_editor_input(
        schedule_preset="every_3_days",
        station_1_duration=0,
        station_2_duration=0,
    )
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        warned = await handler.async_step_program_edit(user_input)
        assert warned["step_id"] == "program_degenerate_confirm"
        result = await handler.async_step_program_degenerate_confirm(
            {CONFIRM_DEGENERATE: True}
        )

    assert result["type"] == "create_entry"
    coordinator.set_irrigation_program.assert_awaited_once()
    _, program = coordinator.set_irrigation_program.await_args.args
    assert program["period_length"] == 3


@pytest.mark.asyncio
async def test_program_edit_failed_write_then_different_preset_is_applied(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A preset picked after a failed write is honored, not silently ignored."""
    handler, coordinator = _loaded_editor_handler(hass, mock_config_entry)
    coordinator.set_irrigation_program = AsyncMock(
        side_effect=Exception("device unreachable")
    )
    with patch.object(
        SolemOptionsFlowHandler,
        "config_entry",
        new_callable=PropertyMock,
        return_value=mock_config_entry,
    ):
        # Phase 1: pick a preset (preview-only confirm renders).
        first = await handler.async_step_program_edit(
            _program_editor_input(schedule_preset="every_3_days")
        )
        assert first["step_id"] == "program_apply_confirm"
        # Phase 2: confirm; the write fails and re-renders the editor.
        second = await handler.async_step_program_apply_confirm(
            {"confirm_apply": True}
        )
        assert second["errors"] == {"base": "set_program_failed"}
        # Phase 3: pick a DIFFERENT preset — it must re-apply (fresh
        # preview with the new preset's values), not silently write.
        third = await handler.async_step_program_edit(
            _program_editor_input(schedule_preset="every_2_days")
        )

    assert third["step_id"] == "program_apply_confirm"
    # The only write attempt is the expected failed one from phase 2 —
    # phase 3 must re-apply the new preset, not silently write.
    assert coordinator.set_irrigation_program.await_count == 1
    assert handler._pending_program is not None
    assert handler._pending_program["period_length"] == 2


@pytest.mark.parametrize(
    "translation_file",
    [
        Path("custom_components/solem_blip/strings.json"),
        Path("custom_components/solem_blip/translations/en.json"),
        Path("custom_components/solem_blip/translations/it.json"),
        Path("custom_components/solem_blip/translations/fr.json"),
    ],
)
def test_program_edit_preset_translations_exist(translation_file: Path) -> None:
    """Preset field label and selector options exist in all four locales."""
    translations = json.loads(translation_file.read_text())

    data = translations["options"]["step"]["program_edit"]["data"]
    assert data["schedule_preset"]

    options = translations["selector"]["schedule_preset_selector"]["options"]
    assert set(options) == {
        "none",
        "every_day",
        "even_days",
        "odd_days",
        "every_2_days",
        "every_3_days",
        "every_4_days",
    }
    assert all(options.values())


@pytest.mark.parametrize(
    "translation_file",
    [
        Path("custom_components/solem_blip/strings.json"),
        Path("custom_components/solem_blip/translations/en.json"),
        Path("custom_components/solem_blip/translations/it.json"),
        Path("custom_components/solem_blip/translations/fr.json"),
    ],
)
def test_program_edit_confirm_steps_translations_exist(
    translation_file: Path,
) -> None:
    """The two confirm steps and their labels exist in all four locales."""
    translations = json.loads(translation_file.read_text())
    steps = translations["options"]["step"]

    for step_id in ("program_apply_confirm", "program_degenerate_confirm"):
        step = steps[step_id]
        assert step["title"]
        assert "{preview}" in step["description"]

    assert steps["program_apply_confirm"]["data"]["confirm_apply"]
    assert steps["program_degenerate_confirm"]["data"]["confirm_degenerate"]


def _stored_periodic_program(synchro_day: int) -> IrrigationProgram:
    """A stored periodic program with the given phase, anchor 2026-09-01."""
    return {
        "name": "Vasi",
        "inter_station_delay": 0,
        "water_budget": 100,
        "cycle": 4,
        "week_days": 0x05,
        "period_length": 3,
        "synchro_day": synchro_day,
        "period_start_date": date(2026, 9, 1),
        "start_times": [1060] + [None] * 7,
        "station_durations": [1200, 0],
    }


def _flat_editor_input(**overrides: object) -> dict[str, object]:
    """Editor input flattened to the parser's namespace (as the flow does)."""
    data = _program_editor_input(**overrides)
    periodic = data.pop("periodic")
    return {**periodic, **data}


def _parse_after_anchor_shift(period_start_date: str) -> IrrigationProgram:
    """Re-parse the editor form after moving the anchor to period_start_date."""
    previous = _stored_periodic_program(synchro_day=1)
    data = _flat_editor_input(
        period_start_date=period_start_date,
        period_length=3,
    )
    data.pop("synchro_day")
    return _parse_program_input(data, num_stations=2, current_program=previous)


def test_parse_program_input_derives_synchro_day_from_anchor_shift() -> None:
    """synchro_day shifts WITH the anchor, not just by the delta alone."""
    parsed = _parse_after_anchor_shift("2026-09-03")

    # (stored 1 + 2-day shift) % period 3 = 0; delta-only would give 2.
    assert parsed["synchro_day"] == 0


def test_parse_program_input_negative_anchor_shift() -> None:
    """A backward anchor shift wraps via Python's non-negative modulo."""
    parsed = _parse_after_anchor_shift("2026-08-31")

    # (stored 1 + (-1)-day shift) % period 3 = 0, not negative.
    assert parsed["synchro_day"] == 0


def test_parse_program_input_keeps_synchro_day_when_anchor_unchanged() -> None:
    """Unchanged anchor date keeps the stored synchro_day, ignoring the form."""
    previous = _stored_periodic_program(synchro_day=1)
    data = _flat_editor_input(
        period_start_date="2026-09-01",
        period_length=5,
    )
    data["synchro_day"] = 0

    parsed = _parse_program_input(data, num_stations=2, current_program=previous)

    assert parsed["synchro_day"] == 1
