"""Config flow for the Solem BL-IP integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
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

from solem_blip_ble import IrrigationProgram, SolemClient, SolemConnectionError

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
    PROGRAM_LABELS,
    SOLEM_API_MOCK,
)
from .config_entry import MyConfigEntry

_LOGGER = logging.getLogger(__name__)

ATTR_CYCLE = "cycle"
ATTR_INTER_STATION_DELAY = "inter_station_delay"
ATTR_NAME = "name"
ATTR_PERIOD_LENGTH = "period_length"
ATTR_PERIOD_START_DATE = "period_start_date"
ATTR_PROGRAM = "program"
ATTR_SYNCHRO_DAY = "synchro_day"
ATTR_WATER_BUDGET = "water_budget"
ATTR_WEEK_DAYS = "week_days"
MAX_PROGRAM_DURATION_SECONDS = 0xFFFFFF
SECONDS_PER_MINUTE = 60
MAX_PROGRAM_DURATION_MINUTES = MAX_PROGRAM_DURATION_SECONDS / SECONDS_PER_MINUTE

MENU_SETTINGS = "settings"
MENU_EDIT_PROGRAM = "program_select"

_CYCLES = {
    "custom": 0,
    "even": 1,
    "odd": 2,
    "odd_31": 3,
    "periodic": 4,
}
_CYCLE_NAMES = {value: key for key, value in _CYCLES.items()}
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


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

    _selected_program_index: int = 0

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu.

        ``user_input`` support is kept for older tests/direct callers that submit
        the original settings form directly to the init step.
        """
        if user_input is not None:
            options = self.config_entry.options | user_input
            return self.async_create_entry(title="", data=options)

        return self.async_show_menu(
            step_id="init",
            menu_options=[MENU_SETTINGS, MENU_EDIT_PROGRAM],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit integration polling and BLE options."""
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

        return self.async_show_form(step_id="settings", data_schema=data_schema)

    async def async_step_program_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which on-device program to edit."""
        if user_input is not None:
            self._selected_program_index = int(user_input[ATTR_PROGRAM]) - 1
            return await self.async_step_program_edit()

        return self.async_show_form(
            step_id="program_select",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_PROGRAM, default=1): selector(
                        {
                            "select": {
                                "options": self._program_select_options(
                                    self._coordinator
                                ),
                                "mode": "dropdown",
                            }
                        }
                    )
                }
            ),
        )

    async def async_step_program_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit and write one persisted on-device irrigation program."""
        errors: dict[str, str] = {}
        coordinator = self._coordinator
        if coordinator is None:
            return self.async_show_form(
                step_id="program_edit",
                data_schema=self._program_schema(None),
                errors={"base": "not_loaded"},
            )

        program_index = self._selected_program_index
        if user_input is not None:
            if coordinator._irrigation_active or coordinator._is_watering:
                errors["base"] = "set_program_while_watering"
            else:
                try:
                    program = self._program_from_options_input(
                        user_input,
                        num_stations=coordinator.num_stations,
                        station_names=self._station_names(coordinator),
                    )
                    await coordinator.set_irrigation_program(program_index, program)
                except vol.Invalid:
                    errors["base"] = "invalid_program"
                except Exception:
                    _LOGGER.exception(
                        "Failed to update Program %s from options flow",
                        PROGRAM_LABELS[program_index],
                    )
                    errors["base"] = "set_program_failed"
                else:
                    return self.async_create_entry(
                        title="",
                        data=dict(self.config_entry.options),
                    )

        current_program = coordinator.irrigation_programs.get(program_index)
        return self.async_show_form(
            step_id="program_edit",
            data_schema=self._program_schema(
                current_program,
                station_names=self._station_names(coordinator),
            ),
            errors=errors,
            description_placeholders={
                "program": self._program_option_label(coordinator, program_index),
            },
        )

    @property
    def _coordinator(self) -> Any | None:
        config_entry = self.config_entry
        runtime_data = getattr(config_entry, "runtime_data", None)
        if runtime_data is None:
            return None
        return runtime_data.coordinator

    def _program_schema(
        self,
        program: IrrigationProgram | None,
        *,
        station_names: dict[int, str] | None = None,
    ) -> vol.Schema:
        num_stations = int(self.config_entry.data.get(NUM_STATIONS, MIN_NUM_STATIONS))
        defaults = self._program_defaults(program, num_stations=num_stations)
        fields: dict[Any, Any] = {
            vol.Required(ATTR_NAME, default=defaults[ATTR_NAME]): str,
            vol.Required(ATTR_CYCLE, default=defaults[ATTR_CYCLE]): selector(
                {
                    "select": {
                        "options": list(_CYCLES),
                        "mode": "dropdown",
                        "translation_key": "cycle_selector",
                    }
                }
            ),
            vol.Required(
                ATTR_WEEK_DAYS,
                default=defaults[ATTR_WEEK_DAYS],
            ): selector(
                {
                    "select": {
                        "multiple": True,
                        "options": list(_WEEKDAYS),
                        "translation_key": "weekday_selector",
                    }
                }
            ),
            vol.Required(
                ATTR_PERIOD_START_DATE,
                default=defaults[ATTR_PERIOD_START_DATE],
            ): selector({"date": {}}),
            vol.Required(
                ATTR_PERIOD_LENGTH,
                default=defaults[ATTR_PERIOD_LENGTH],
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
            vol.Required(
                ATTR_SYNCHRO_DAY,
                default=defaults[ATTR_SYNCHRO_DAY],
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Required(
                ATTR_WATER_BUDGET,
                default=defaults[ATTR_WATER_BUDGET],
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
            vol.Required(
                ATTR_INTER_STATION_DELAY,
                default=defaults[ATTR_INTER_STATION_DELAY],
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        }
        for slot in range(8):
            key = self._start_key(slot)
            fields[vol.Optional(key, default=defaults[key])] = str
        for station in range(1, num_stations + 1):
            default_key = self._station_key(station)
            key = self._station_duration_key(station, station_names=station_names)
            fields[vol.Required(key, default=defaults[default_key])] = vol.All(
                vol.Coerce(float),
                vol.Range(min=0, max=MAX_PROGRAM_DURATION_MINUTES),
            )
        return vol.Schema(fields)

    def _program_defaults(
        self,
        program: IrrigationProgram | None,
        *,
        num_stations: int,
    ) -> dict[str, Any]:
        program_data: dict[str, Any] = dict(program) if program is not None else {}
        station_durations = list(program_data.get("station_durations", []))
        station_durations.extend([0] * (num_stations - len(station_durations)))
        period_start_date = program_data.get("period_start_date")
        defaults: dict[str, Any] = {
            ATTR_NAME: program_data.get("name", ""),
            ATTR_CYCLE: _CYCLE_NAMES.get(int(program_data.get("cycle", 0)), "custom"),
            ATTR_WEEK_DAYS: self._weekdays_from_mask(
                int(program_data.get("week_days", 0x7F))
            ),
            ATTR_PERIOD_START_DATE: period_start_date or date.today(),
            ATTR_PERIOD_LENGTH: int(program_data.get("period_length", 1)),
            ATTR_SYNCHRO_DAY: int(program_data.get("synchro_day", 0)),
            ATTR_WATER_BUDGET: int(program_data.get("water_budget", 100)),
            ATTR_INTER_STATION_DELAY: int(program_data.get("inter_station_delay", 0)),
        }
        start_times = list(program_data.get("start_times", []))
        start_times.extend([None] * (8 - len(start_times)))
        for slot, minutes in enumerate(start_times[:8]):
            defaults[self._start_key(slot)] = self._format_minutes(minutes)
        for station in range(1, num_stations + 1):
            defaults[self._station_key(station)] = self._duration_minutes(
                int(station_durations[station - 1])
            )
        return defaults

    def _program_from_options_input(
        self,
        data: dict[str, Any],
        *,
        num_stations: int,
        station_names: dict[int, str] | None = None,
    ) -> IrrigationProgram:
        start_times = [
            self._parse_optional_time(data.get(self._start_key(slot), ""))
            for slot in range(8)
        ]
        period_start_date = data[ATTR_PERIOD_START_DATE]
        if isinstance(period_start_date, str):
            period_start_date = date.fromisoformat(period_start_date)
        return {
            "name": str(data[ATTR_NAME]),
            "inter_station_delay": int(data[ATTR_INTER_STATION_DELAY]),
            "water_budget": int(data[ATTR_WATER_BUDGET]),
            "cycle": _CYCLES[str(data[ATTR_CYCLE])],
            "week_days": self._weekdays_mask(list(data[ATTR_WEEK_DAYS])),
            "period_length": int(data[ATTR_PERIOD_LENGTH]),
            "synchro_day": int(data[ATTR_SYNCHRO_DAY]),
            "period_start_date": period_start_date,
            "start_times": start_times,
            "station_durations": [
                self._duration_seconds(
                    self._station_duration_value(
                        data,
                        station,
                        station_names=station_names,
                    )
                )
                for station in range(1, num_stations + 1)
            ],
        }

    @staticmethod
    def _start_key(slot: int) -> str:
        return f"start_time_{slot + 1}"

    @staticmethod
    def _station_key(station: int) -> str:
        return f"station_{station}_duration"

    @staticmethod
    def _station_duration_key(
        station: int,
        *,
        station_names: dict[int, str] | None = None,
    ) -> str:
        name = (station_names or {}).get(station)
        if not name:
            return SolemOptionsFlowHandler._station_key(station)
        return f"{name} (station {station}) duration (minutes)"

    @staticmethod
    def _station_duration_value(
        data: dict[str, Any],
        station: int,
        *,
        station_names: dict[int, str] | None = None,
    ) -> Any:
        key = SolemOptionsFlowHandler._station_duration_key(
            station,
            station_names=station_names,
        )
        if key in data:
            return data[key]
        return data[SolemOptionsFlowHandler._station_key(station)]

    @staticmethod
    def _station_names(coordinator: Any | None) -> dict[int, str]:
        station_names = getattr(coordinator, "station_names", None)
        return station_names if isinstance(station_names, dict) else {}

    def _program_select_options(self, coordinator: Any | None) -> list[dict[str, str]]:
        return [
            {
                "value": str(index + 1),
                "label": self._program_option_label(coordinator, index),
            }
            for index in range(len(PROGRAM_LABELS))
        ]

    @staticmethod
    def _program_option_label(coordinator: Any | None, program_index: int) -> str:
        slot_name = f"Program {PROGRAM_LABELS[program_index]}"
        programs = getattr(coordinator, "irrigation_programs", None)
        if not isinstance(programs, dict):
            return slot_name
        program = programs.get(program_index)
        if not isinstance(program, dict):
            return slot_name
        name = str(program.get("name") or "").strip()
        if not name or name == slot_name:
            return slot_name
        return f"{slot_name} - {name}"

    @staticmethod
    def _duration_minutes(seconds: int) -> int | float:
        minutes, remaining_seconds = divmod(seconds, SECONDS_PER_MINUTE)
        if remaining_seconds:
            return round(seconds / SECONDS_PER_MINUTE, 2)
        return minutes

    @staticmethod
    def _duration_seconds(minutes: Any) -> int:
        seconds = round(float(minutes) * SECONDS_PER_MINUTE)
        if seconds < 0 or seconds > MAX_PROGRAM_DURATION_SECONDS:
            raise vol.Invalid(
                "station duration must be between 0 and 279620.25 minutes"
            )
        return seconds

    @staticmethod
    def _format_minutes(minutes: int | None) -> str:
        if minutes is None:
            return ""
        hours, minute = divmod(minutes, 60)
        return f"{hours:02d}:{minute:02d}"

    @staticmethod
    def _parse_optional_time(value: Any) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            hours_text, minutes_text = text.split(":", 1)
            hours = int(hours_text)
            minutes = int(minutes_text)
        except ValueError as exc:
            raise vol.Invalid("start times must use HH:MM") from exc
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise vol.Invalid("start times must use HH:MM between 00:00 and 23:59")
        return hours * 60 + minutes

    @staticmethod
    def _weekdays_from_mask(mask: int) -> list[str]:
        return [day for day, index in _WEEKDAYS.items() if mask & (1 << index)]

    @staticmethod
    def _weekdays_mask(days: list[Any]) -> int:
        mask = 0
        for day in days:
            mask |= 1 << _WEEKDAYS[str(day)]
        return mask


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class CannotConnectSlots(CannotConnect):
    """Error to indicate Bluetooth adapters/proxies are out of connection slots."""
