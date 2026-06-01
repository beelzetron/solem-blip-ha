"""Config flow for the Solem BL-IP integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import selector

from solem_blip_ble import SolemClient, SolemConnectionError

from .bluetooth import (
    async_get_connectable_device,
    async_is_device_discovered,
    async_scan_devices,
)
from .const import (
    BLUETOOTH_DEFAULT_TIMEOUT,
    BLUETOOTH_MAX_TIMEOUT,
    BLUETOOTH_MIN_TIMEOUT,
    BLUETOOTH_TIMEOUT,
    CONFIG_FLOW_BLUETOOTH_TIMEOUT,
    CONFIG_FLOW_CONNECT_RETRIES,
    CONFIG_FLOW_CONNECT_RETRY_DELAY,
    CONTROLLER_MAC_ADDRESS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_NUM_STATIONS,
    MAX_SCAN_INTERVAL,
    MIN_NUM_STATIONS,
    MIN_SCAN_INTERVAL,
    NUM_STATIONS,
    SOLEM_API_MOCK,
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate BLE connectivity to the selected controller."""
    address = data[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[1]
    _LOGGER.debug("Validating BLE connection to %s", address)

    def _resolve_ble_device() -> Any | None:
        return async_get_connectable_device(hass, address)

    if _resolve_ble_device() is None:
        raise CannotConnect

    api = SolemClient(
        address,
        CONFIG_FLOW_BLUETOOTH_TIMEOUT,
        ble_device_resolver=_resolve_ble_device,
    )

    last_err: Exception | None = None
    try:
        for attempt in range(CONFIG_FLOW_CONNECT_RETRIES):
            try:
                await api.connect()
                _LOGGER.debug("Connected to Bluetooth controller %s", address)
                return {"title": "Solem BL-IP"}
            except SolemConnectionError as err:
                last_err = err
                if (
                    "connection slots" in str(err).lower()
                    and attempt < CONFIG_FLOW_CONNECT_RETRIES - 1
                ):
                    _LOGGER.debug(
                        "BLE connection slots busy for %s, retrying (%s/%s)",
                        address,
                        attempt + 1,
                        CONFIG_FLOW_CONNECT_RETRIES,
                    )
                    await asyncio.sleep(CONFIG_FLOW_CONNECT_RETRY_DELAY)
                    continue
                break
    finally:
        await api.disconnect()

    if last_err is None:
        raise CannotConnect
    if "connection slots" in str(last_err).lower():
        if async_is_device_discovered(hass, address):
            _LOGGER.warning(
                "%s - Live BLE connect failed because adapters/proxies are out "
                "of connection slots, but the controller is visible in Home "
                "Assistant discovery. Proceeding with setup; the integration "
                "will connect on first poll.",
                address,
            )
            return {"title": "Solem BL-IP"}
        raise CannotConnectSlots from last_err
    raise CannotConnect from last_err


class SolemConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solem BL-IP."""

    VERSION = 2
    _discovered_controller: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SolemOptionsFlowHandler:
        """Return the options flow handler."""
        return SolemOptionsFlowHandler()

    def _build_schema(
        self,
        *,
        controller_default: str | None = None,
        num_stations_default: int = 1,
        bt_options: list[dict[str, str]],
    ) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONTROLLER_MAC_ADDRESS,
                    default=controller_default,
                ): selector(
                    {
                        "select": {
                            "options": bt_options,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(NUM_STATIONS, default=num_stations_default): vol.All(
                    vol.Coerce(int),
                    vol.Clamp(min=MIN_NUM_STATIONS, max=MAX_NUM_STATIONS),
                ),
            }
        )

    async def async_step_bluetooth(self, discovery_info: Any) -> ConfigFlowResult:
        """Handle a controller discovered by Home Assistant Bluetooth."""
        address = discovery_info.address.upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._discovered_controller = (
            f"{discovery_info.name or 'Solem BL-IP'} - {address}"
        )
        self.context["title_placeholders"] = {"name": self._discovered_controller}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup for a Bluetooth-discovered controller."""
        assert self._discovered_controller is not None
        errors: dict[str, str] = {}
        data = {
            CONTROLLER_MAC_ADDRESS: self._discovered_controller,
            NUM_STATIONS: 2,
        }
        if user_input is not None:
            data[NUM_STATIONS] = user_input[NUM_STATIONS]
            try:
                await validate_input(self.hass, data)
            except CannotConnectSlots:
                errors["base"] = "cannot_connect_slots"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=self._discovered_controller,
                    data=data,
                )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        NUM_STATIONS,
                        default=data[NUM_STATIONS],
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Clamp(min=MIN_NUM_STATIONS, max=MAX_NUM_STATIONS),
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"name": self._discovered_controller},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnectSlots:
                errors["base"] = "cannot_connect_slots"
                _LOGGER.exception("Bluetooth connection slots unavailable")
            except CannotConnect:
                errors["base"] = "cannot_connect"
                _LOGGER.exception("Cannot connect")
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    user_input[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[1].upper()
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONTROLLER_MAC_ADDRESS],
                    data=user_input,
                )

        existing_entries = {
            entry.data.get(CONTROLLER_MAC_ADDRESS)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        bt_devices = await async_scan_devices(self.hass)
        options = [
            {
                "value": f"{device.name or 'Unknown'} - {device.address}",
                "label": f"{device.name or 'Unknown'} - {device.address}",
            }
            for device in bt_devices
            if f"{device.name or 'Unknown'} - {device.address}" not in existing_entries
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=self._build_schema(bt_options=options),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the station count for an existing entry."""
        config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        assert config_entry is not None

        if user_input is not None:
            return self.async_update_reload_and_abort(
                config_entry,
                unique_id=config_entry.unique_id,
                data={
                    **config_entry.data,
                    NUM_STATIONS: user_input[NUM_STATIONS],
                },
                reason="reconfigure_successful",
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        NUM_STATIONS,
                        default=config_entry.data[NUM_STATIONS],
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Clamp(min=MIN_NUM_STATIONS, max=MAX_NUM_STATIONS),
                    ),
                }
            ),
        )


class SolemOptionsFlowHandler(OptionsFlow):
    """Handle integration options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self.config_entry.options | user_input
            return self.async_create_entry(title="", data=options)

        options = dict(self.config_entry.options)
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Clamp(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Required(
                    BLUETOOTH_TIMEOUT,
                    default=options.get(
                        BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Clamp(min=BLUETOOTH_MIN_TIMEOUT, max=BLUETOOTH_MAX_TIMEOUT),
                ),
                vol.Required(
                    SOLEM_API_MOCK,
                    default=options.get(SOLEM_API_MOCK, "false"),
                ): selector(
                    {
                        "select": {
                            "options": ["false", "true"],
                            "mode": "dropdown",
                            "translation_key": "true_false_selector",
                        }
                    }
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class CannotConnectSlots(CannotConnect):
    """Error to indicate Bluetooth adapters/proxies are out of connection slots."""
