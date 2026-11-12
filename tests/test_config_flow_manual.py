"""Manual MAC entry tests for the Solem BL-IP config flow (issue #172).

The controller of a user who filed the issue advertises no V5 service UUID,
so ``async_scan_devices`` returns nothing and the setup dropdown is empty.
The manual path lets the user type the MAC printed on the controller; the
runtime resolves the device by address, so the UUID is never needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solem_blip.config_flow import (
    MANUAL_CONTROLLER_ENTRY,
    SETUP_NUM_STATIONS_FLOOR,
    CannotConnect,
    CannotConnectSlots,
    SolemConfigFlow,
)
from custom_components.solem_blip.const import (
    CONTROLLER_MAC_ADDRESS,
    DOMAIN,
    NUM_STATIONS,
)

MANUAL_LABEL = "Enter MAC address manually"
CANONICAL_MAC = "AA:BB:CC:DD:EE:FF"


def _controller_option(result: dict) -> dict:
    """Return the serialized controller selector config from a user step."""
    for key, value in result["data_schema"].schema.items():
        if str(key.schema) == CONTROLLER_MAC_ADDRESS:
            return value.config
    raise AssertionError("controller_mac_address field missing from the form")


def _manual_form_default(result: dict) -> Any:
    """Return the schema default of the manual MAC field."""
    for key in result["data_schema"].schema:
        if str(key.schema) == CONTROLLER_MAC_ADDRESS:
            return key.default()
    raise AssertionError("controller_mac_address field missing from the form")


def _manual_flow(hass: HomeAssistant) -> SolemConfigFlow:
    """Build a config flow instance wired like the existing flow tests."""
    flow = SolemConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    return flow


# ---------------------------------------------------------------------------
# User step: the manual option is always offered, last in the dropdown.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_step_always_offers_manual_entry_without_devices(
    hass: HomeAssistant,
) -> None:
    """An empty scan result still offers the manual MAC entry option."""
    flow = _manual_flow(hass)

    with patch(
        "custom_components.solem_blip.config_flow.async_scan_devices",
        new=AsyncMock(return_value=[]),
    ):
        result = await flow.async_step_user()

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    options = _controller_option(result)["options"]
    assert options[-1] == {"value": MANUAL_CONTROLLER_ENTRY, "label": MANUAL_LABEL}
    assert [option["value"] for option in options].count(MANUAL_CONTROLLER_ENTRY) == 1


@pytest.mark.asyncio
async def test_user_step_offers_manual_entry_after_discovered_devices(
    hass: HomeAssistant,
) -> None:
    """Discovered controllers are listed first; the manual option stays last."""
    flow = _manual_flow(hass)
    device = SimpleNamespace(name="Solem BL-IP", address=CANONICAL_MAC)

    with patch(
        "custom_components.solem_blip.config_flow.async_scan_devices",
        new=AsyncMock(return_value=[device]),
    ):
        result = await flow.async_step_user()

    options = _controller_option(result)["options"]
    assert options[0] == {
        "value": f"Solem BL-IP - {CANONICAL_MAC}",
        "label": f"Solem BL-IP - {CANONICAL_MAC}",
    }
    assert options[-1] == {"value": MANUAL_CONTROLLER_ENTRY, "label": MANUAL_LABEL}


@pytest.mark.asyncio
async def test_user_step_keeps_manual_entry_when_device_already_configured(
    hass: HomeAssistant,
) -> None:
    """Filtering an already configured controller keeps the manual option."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=CANONICAL_MAC,
        data={
            CONTROLLER_MAC_ADDRESS: f"Solem BL-IP - {CANONICAL_MAC}",
            NUM_STATIONS: 2,
        },
    ).add_to_hass(hass)
    flow = _manual_flow(hass)
    device = SimpleNamespace(name="Solem BL-IP", address=CANONICAL_MAC)

    with patch(
        "custom_components.solem_blip.config_flow.async_scan_devices",
        new=AsyncMock(return_value=[device]),
    ):
        result = await flow.async_step_user()

    options = _controller_option(result)["options"]
    assert options == [{"value": MANUAL_CONTROLLER_ENTRY, "label": MANUAL_LABEL}]


# ---------------------------------------------------------------------------
# User step: the sentinel routes to the manual step, before any MAC parsing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_step_manual_sentinel_opens_manual_form(
    hass: HomeAssistant,
) -> None:
    """Submitting the sentinel shows the manual MAC form without validating."""
    flow = _manual_flow(hass)

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(return_value={"title": "Solem BL-IP", "num_stations": 2}),
    ) as mock_validate, patch(
        "custom_components.solem_blip.config_flow.async_scan_devices",
        new=AsyncMock(return_value=[]),
    ):
        result = await flow.async_step_user(
            {CONTROLLER_MAC_ADDRESS: MANUAL_CONTROLLER_ENTRY}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "manual"
    mock_validate.assert_not_awaited()
    flow.async_create_entry.assert_not_called()


# ---------------------------------------------------------------------------
# Manual step: form rendering and input validation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_step_shows_form(hass: HomeAssistant) -> None:
    """The manual step renders a free-text MAC field."""
    flow = _manual_flow(hass)

    result = await flow.async_step_manual()

    assert result["type"] == "form"
    assert result["step_id"] == "manual"
    fields = {str(key.schema): value for key, value in result["data_schema"].schema.items()}
    assert CONTROLLER_MAC_ADDRESS in fields
    assert fields[CONTROLLER_MAC_ADDRESS] is str
    assert result["data_schema"]({CONTROLLER_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"}) == {
        CONTROLLER_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_input",
    [
        "AA:BB:CC:DD:EE:FF",
        "aa-bb-cc-dd-ee-ff",
        "AA BB CC DD EE FF",
        "aabbccddeeff",
        "  aa:bb:cc:dd:ee:ff  ",
        "Solem BL-IP - aa:bb:cc:dd:ee:ff",
        "BL1IP-A1B2C3 - AABBCCDDEEFF",
    ],
)
async def test_manual_step_creates_entry_for_accepted_formats(
    hass: HomeAssistant, raw_input: str
) -> None:
    """Every accepted MAC format normalizes and creates the config entry."""
    flow = _manual_flow(hass)
    normalized = f"Solem BL-IP - {CANONICAL_MAC}"

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(return_value={"title": "Solem BL-IP", "num_stations": 4}),
    ) as mock_validate:
        result = await flow.async_step_manual({CONTROLLER_MAC_ADDRESS: raw_input})

    mock_validate.assert_awaited_once_with(
        hass,
        {
            CONTROLLER_MAC_ADDRESS: normalized,
            NUM_STATIONS: SETUP_NUM_STATIONS_FLOOR,
        },
    )
    flow.async_set_unique_id.assert_awaited_once_with(CANONICAL_MAC)
    flow._abort_if_unique_id_configured.assert_called_once_with()
    flow.async_create_entry.assert_called_once_with(
        title=normalized,
        data={CONTROLLER_MAC_ADDRESS: normalized, NUM_STATIONS: 4},
    )
    assert result == {"type": "create_entry"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_input",
    [
        "",
        "   ",
        "AA:BB:CC:DD:EE",
        "AA:BB:CC:DD:EE:FF:00",
        "GG:BB:CC:DD:EE:FF",
        "AA:BB-CC:DD:EE:FF",
        "AABBCCDDEEFF00",
        "Solem BL-IP - ",
        "Solem BL-IP - not-a-mac",
    ],
)
async def test_manual_step_rejects_invalid_mac(
    hass: HomeAssistant, raw_input: str
) -> None:
    """Invalid input re-renders the manual form with the invalid_mac error."""
    flow = _manual_flow(hass)

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(return_value={"title": "Solem BL-IP", "num_stations": 2}),
    ) as mock_validate:
        result = await flow.async_step_manual({CONTROLLER_MAC_ADDRESS: raw_input})

    assert result["type"] == "form"
    assert result["step_id"] == "manual"
    assert result["errors"]["base"] == "invalid_mac"
    mock_validate.assert_not_awaited()
    flow.async_create_entry.assert_not_called()
    flow.async_set_unique_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "error_key"),
    [
        (CannotConnectSlots(), "cannot_connect_slots"),
        (CannotConnect(), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_manual_step_maps_validation_errors(
    hass: HomeAssistant, exc: Exception, error_key: str
) -> None:
    """The manual step maps validation failures exactly like the user step."""
    flow = _manual_flow(hass)

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(side_effect=exc),
    ):
        result = await flow.async_step_manual(
            {CONTROLLER_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "manual"
    assert result["errors"]["base"] == error_key
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_manual_step_aborts_on_duplicate_unique_id(hass: HomeAssistant) -> None:
    """A duplicate unique id aborts before any BLE validation attempt."""
    from homeassistant.data_entry_flow import AbortFlow

    flow = _manual_flow(hass)
    flow._abort_if_unique_id_configured = MagicMock(
        side_effect=AbortFlow("already_configured")
    )

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(return_value={"title": "Solem BL-IP", "num_stations": 2}),
    ) as mock_validate:
        with pytest.raises(AbortFlow) as err:
            await flow.async_step_manual(
                {CONTROLLER_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"}
            )

    assert err.value.reason == "already_configured"
    flow.async_set_unique_id.assert_awaited_once_with(CANONICAL_MAC)
    mock_validate.assert_not_awaited()
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_manual_step_aborts_before_validation_when_controller_unreachable(
    hass: HomeAssistant,
) -> None:
    """An already-configured MAC aborts even when validation would fail.

    This pins the ordering: the duplicate check runs before validate_input, so
    an unreachable controller never costs the BLE timeout x retry budget.
    """
    from homeassistant.data_entry_flow import AbortFlow

    flow = _manual_flow(hass)
    flow._abort_if_unique_id_configured = MagicMock(
        side_effect=AbortFlow("already_configured")
    )

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(side_effect=CannotConnect()),
    ) as mock_validate:
        with pytest.raises(AbortFlow) as err:
            await flow.async_step_manual(
                {CONTROLLER_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"}
            )

    assert err.value.reason == "already_configured"
    flow.async_set_unique_id.assert_awaited_once_with(CANONICAL_MAC)
    mock_validate.assert_not_awaited()
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_manual_step_rerenders_raw_input_after_invalid_mac(
    hass: HomeAssistant,
) -> None:
    """The invalid_mac re-render prefills the raw typed text, not None."""
    flow = _manual_flow(hass)
    raw_input = "GG:BB:CC:DD:EE:FF"

    result = await flow.async_step_manual({CONTROLLER_MAC_ADDRESS: raw_input})

    assert result["errors"]["base"] == "invalid_mac"
    assert _manual_form_default(result) == raw_input


@pytest.mark.asyncio
async def test_manual_step_rerenders_normalized_mac_after_connection_error(
    hass: HomeAssistant,
) -> None:
    """A connection error re-renders with the normalized MAC prefilled."""
    flow = _manual_flow(hass)

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(side_effect=CannotConnect()),
    ):
        result = await flow.async_step_manual(
            {CONTROLLER_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"}
        )

    assert result["errors"]["base"] == "cannot_connect"
    assert _manual_form_default(result) == CANONICAL_MAC


@pytest.mark.asyncio
async def test_manual_step_aborts_against_real_configured_entry(
    hass: HomeAssistant,
) -> None:
    """Real unique-id detection aborts when the controller is already set up."""
    from homeassistant.data_entry_flow import AbortFlow

    MockConfigEntry(
        domain=DOMAIN,
        unique_id=CANONICAL_MAC,
        data={
            CONTROLLER_MAC_ADDRESS: f"Solem BL-IP - {CANONICAL_MAC}",
            NUM_STATIONS: 2,
        },
    ).add_to_hass(hass)

    flow = SolemConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {}
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    with patch(
        "custom_components.solem_blip.config_flow.validate_input",
        new=AsyncMock(return_value={"title": "Solem BL-IP", "num_stations": 2}),
    ):
        with pytest.raises(AbortFlow) as err:
            await flow.async_step_manual(
                {CONTROLLER_MAC_ADDRESS: "aa-bb-cc-dd-ee-ff"}
            )

    assert err.value.reason == "already_configured"
    flow.async_create_entry.assert_not_called()
