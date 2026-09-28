"""Config flow for the Solem BL-IP integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import selector
from homeassistant.util import dt as dt_util

from solem_blip_ble import IrrigationProgram, SolemConnectionError

from .bluetooth import (
    async_get_connectable_device,
    async_is_device_discovered,
    async_scan_devices,
)
from .client_factory import create_solem_client
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
    PERSISTENT_CONNECTION,
    PERSISTENT_HOLD_LINK,
    PROGRAM_LABELS,
    SOLEM_API_MOCK,
)
from .config_entry import MyConfigEntry
from .exceptions_map import flow_error_for_exception
from .schedule import (
    format_duration,
    is_degenerate_schedule,
    next_start_datetime,
    schedule_summary,
)
from .station_names import StationNameManager

_LOGGER = logging.getLogger(__name__)

# Entry-data floor for the station count at setup. The device-derived
# count replaces it when the snapshot read succeeds (upward-only); this
# matches the coordinator/migration default (.get(NUM_STATIONS, 2)).
SETUP_NUM_STATIONS_FLOOR = 2

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

# Sentinel for a cleared/disabled start-time slot. The frontend submits an
# untouched time selector as an empty string; the bare TimeSelector rejects
# it, so the schema allows this value explicitly before the selector runs.
_START_TIME_EMPTY = ""


MENU_SETTINGS = "settings"
MENU_EDIT_PROGRAM = "program_select"
MENU_EDIT_STATION_NAMES = "station_select"

ATTR_ACCEPT_CURRENT = "accept_current"
ATTR_STATION = "station"
CONFIRM_DEGENERATE = "confirm_degenerate"
ATTR_SCHEDULE_PRESET = "schedule_preset"

# Schedule presets (issue #129): one submit applies the encoding to the
# parsed program and re-renders with the preview; the re-render resets the
# preset default to "none" so the second submit parses as preset=none and
# writes (two-phase, mirroring confirm_degenerate).
_PRESET_NONE = "none"
_SCHEDULE_PRESETS: dict[str, dict[str, int]] = {
    # Native parity cycles. every_day additionally zeroes the periodic
    # fields back to weekly semantics (period_length 1, synchro_day 0).
    "every_day": {"cycle": 0, "week_days": 0x7F, "period_length": 1, "synchro_day": 0},
    "even_days": {"cycle": 1},
    "odd_days": {"cycle": 2},
    # Anchored periodic presets: the anchor is the form's period_start_date.
    "every_2_days": {"cycle": 4, "period_length": 2},
    "every_3_days": {"cycle": 4, "period_length": 3},
    "every_4_days": {"cycle": 4, "period_length": 4},
}

# Human text for the degenerate-config reasons returned by
# is_degenerate_schedule. These sentences live in code because HA flow
# description placeholders carry raw strings only; translations ride the
# step description (acceptable trade-off for now).
_DEGENERATE_WARNINGS: dict[str, str] = {
    "no_days": "No day of the week is selected - this program can never start.",
    "no_durations": "All station durations are zero - nothing would be watered.",
    "no_starts": "All start times are cleared - this program can never start.",
}

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


async def _derive_station_count(api: Any, fallback: int) -> int:
    """Read the device-derived station count on the validation connection.

    Up to two polling-safe station-name snapshot reads (solem-blip-ble
    0.3.2b7 hardened read, same safety-grade call the station-name
    options flow uses). The first attempt runs right after the connect
    probe released its link, and single-client devices can kill the
    fresh connection while the previous one is still releasing — so one
    delayed retry is made before falling back. Returns the clamped
    derived count on success; the configured fallback when the client
    does not expose a derived count (mock mode) or the reads fail — the
    runtime upward-only adoption remains the safety net either way.
    """
    snapshot = None
    for attempt in range(2):
        try:
            snapshot = await api.get_station_name_snapshot()
            break
        except Exception as err:
            if attempt == 0:
                _LOGGER.debug(
                    "%s - Station-name snapshot read failed on the first "
                    "attempt (%s); retrying once after the link settles",
                    getattr(api, "mac_address", "?"),
                    type(err).__name__,
                )
                await asyncio.sleep(CONFIG_FLOW_CONNECT_RETRY_DELAY)
                continue
            _LOGGER.info(
                "%s - Station-name snapshot unavailable during setup (%s); "
                "keeping configured station count %d",
                getattr(api, "mac_address", "?"),
                type(err).__name__,
                fallback,
            )
    count = getattr(snapshot, "station_count", None) if snapshot else None
    if not isinstance(count, int) or count < 1:
        return fallback
    # The derived count is a lower bound (highest *named* output): adopt
    # it upward-only against the configured floor, mirroring
    # adopt_device_station_count — never shrink below what was asked.
    derived = min(max(count, fallback), MAX_NUM_STATIONS)
    if derived != count:
        _LOGGER.info(
            "%s - Device reports %d station(s); station count set to %d",
            getattr(api, "mac_address", "?"),
            count,
            derived,
        )
    return derived


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate BLE connectivity; derive the station count from the device.

    The count is a snapshot read adopted upward-only against the
    configured floor (issue #122); the floor wins when the read fails —
    runtime adoption remains the safety net.
    """
    address = data[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[1]
    configured_stations = int(data.get(NUM_STATIONS, SETUP_NUM_STATIONS_FLOOR))
    _LOGGER.debug("Validating BLE connection to %s", address)

    def _resolve_ble_device() -> Any | None:
        return async_get_connectable_device(hass, address)

    if _resolve_ble_device() is None:
        raise CannotConnect

    api = create_solem_client(
        persistent=False,
        scan_interval=DEFAULT_SCAN_INTERVAL,
        mac_address=address,
        bluetooth_timeout=CONFIG_FLOW_BLUETOOTH_TIMEOUT,
        ble_device_resolver=_resolve_ble_device,
    )

    last_err: Exception | None = None
    connected = False
    for attempt in range(CONFIG_FLOW_CONNECT_RETRIES):
        try:
            await api.connect()
            connected = True
            _LOGGER.debug("Connected to Bluetooth controller %s", address)
            break
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

    if connected:
        num_stations = await _derive_station_count(api, configured_stations)
        return {"title": "Solem BL-IP", "num_stations": num_stations}

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
            return {"title": "Solem BL-IP", "num_stations": configured_stations}
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
        bt_options: list[dict[str, str]],
    ) -> vol.Schema:
        """User-step schema: controller selection only.

        The station count is no longer asked at setup — it is derived from
        the device during validation (issue #122 follow-up).
        """
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
        """Confirm setup for a Bluetooth-discovered controller.

        The station count is device-derived during validation (issue #122
        follow-up); no station-count question is shown.
        """
        assert self._discovered_controller is not None
        errors: dict[str, str] = {}
        data = {
            CONTROLLER_MAC_ADDRESS: self._discovered_controller,
            # Floor only; validate_input replaces it with the device-derived
            # count when the snapshot read succeeds.
            NUM_STATIONS: SETUP_NUM_STATIONS_FLOOR,
        }
        if user_input is not None:
            try:
                info = await validate_input(self.hass, data)
            except CannotConnectSlots:
                errors["base"] = "cannot_connect_slots"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                data[NUM_STATIONS] = info["num_stations"]
                return self.async_create_entry(
                    title=self._discovered_controller,
                    data=data,
                )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"name": self._discovered_controller},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step.

        The station count is device-derived during validation (issue #122
        follow-up); no station-count question is shown.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(
                    self.hass,
                    {**user_input, NUM_STATIONS: SETUP_NUM_STATIONS_FLOOR},
                )
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
                address = user_input[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[1].upper()
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONTROLLER_MAC_ADDRESS],
                    data={**user_input, NUM_STATIONS: info["num_stations"]},
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
                        default=config_entry.data.get(
                            NUM_STATIONS, SETUP_NUM_STATIONS_FLOOR
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Clamp(min=MIN_NUM_STATIONS, max=MAX_NUM_STATIONS),
                    ),
                }
            ),
        )


class SolemOptionsFlowHandler(OptionsFlowWithReload):
    """Handle integration options."""

    _selected_program_index: int = 0
    # Two-phase preset guard (issue #129): True between a preset apply
    # re-render and the next submit, so a re-rendered default preset value
    # (or a stray resubmit carrying the preset) cannot re-apply forever.
    _preset_applied: bool = False

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
            menu_options=[
                MENU_SETTINGS,
                MENU_EDIT_PROGRAM,
                MENU_EDIT_STATION_NAMES,
            ],
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
                    PERSISTENT_CONNECTION,
                    default=options.get(PERSISTENT_CONNECTION, False),
                ): selector(
                    {
                        "boolean": {},
                    }
                ),
                vol.Required(
                    PERSISTENT_HOLD_LINK,
                    default=options.get(PERSISTENT_HOLD_LINK, False),
                ): selector(
                    {
                        "boolean": {},
                    }
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
        program: IrrigationProgram | None = None
        if user_input is not None:
            if coordinator._irrigation_active or coordinator._is_watering:
                errors["base"] = "set_program_while_watering"
            else:
                try:
                    program = self._program_from_options_input(
                        user_input,
                        num_stations=coordinator.num_stations,
                        station_names=self._station_names(coordinator),
                        current_program=coordinator.irrigation_programs.get(
                            program_index
                        ),
                    )
                except vol.Invalid:
                    errors["base"] = "invalid_program"
                else:
                    preset_result = self._preset_apply_result(
                        user_input, coordinator, program_index
                    )
                    if preset_result is not None:
                        return preset_result
                    reason = is_degenerate_schedule(program)
                    if reason is not None and not user_input.get(
                        CONFIRM_DEGENERATE, False
                    ):
                        # Non-blocking warning (issue #129): re-render with
                        # the submitted values, a schedule preview, the
                        # warning, and a confirm checkbox. No write yet.
                        return self._show_degenerate_warning(
                            program, coordinator, program_index, reason
                        )
                    return await self._attempt_write(program_index, program)

        current_program = coordinator.irrigation_programs.get(program_index)
        if user_input is None:
            # Fresh render (menu entry): any pending preset-apply state is
            # stale — the user restarted the edit.
            self._preset_applied = False
        return self.async_show_form(
            step_id="program_edit",
            data_schema=self._program_schema(
                current_program,
                station_names=self._station_names(coordinator),
            ),
            errors=errors,
            description_placeholders={
                "program": self._program_option_label(coordinator, program_index),
                # Preview from the just-parsed program when available
                # (validation errors fall here too, via a best-effort partial
                # parse); it must never block or crash a write.
                "preview": self._editor_preview(user_input, program, coordinator),
                "warning": "",
            },
        )

    def _editor_preview(
        self,
        user_input: dict[str, Any] | None,
        program: IrrigationProgram | None,
        coordinator: Any,
    ) -> str:
        """Preview for the re-render, best-effort on validation errors."""
        if program is not None:
            return self._schedule_preview(program, coordinator)
        if user_input is not None:
            return self._schedule_preview(
                self._best_effort_preview_program(
                    user_input, coordinator, self._selected_program_index
                ),
                coordinator,
            )
        return ""

    def _preset_apply_result(
        self,
        user_input: dict[str, Any],
        coordinator: Any,
        program_index: int,
    ) -> ConfigFlowResult | None:
        """Apply a freshly submitted schedule preset (two-phase, issue #129).

        Mirrors the confirm_degenerate pattern: do NOT write. Applies the
        preset to the parsed program, then re-renders with the APPLIED values
        as defaults, the schedule preview, and the preset default reset to
        "none" so the second submit parses as preset=none and writes. The
        flow flag guards a stray re-submit that still carries the preset
        against an infinite apply loop.

        Returns the re-render when a preset was applied, ``None`` when there
        is no preset to apply (caller proceeds to the degenerate/write paths).
        """
        if (
            user_input.get(ATTR_SCHEDULE_PRESET, _PRESET_NONE) == _PRESET_NONE
            or self._preset_applied
        ):
            return None
        self._preset_applied = True
        applied = self._apply_preset(
            str(user_input.get(ATTR_SCHEDULE_PRESET, _PRESET_NONE)),
            user_input,
            num_stations=coordinator.num_stations,
            station_names=self._station_names(coordinator),
        )
        reason = is_degenerate_schedule(applied)
        warning = (
            _DEGENERATE_WARNINGS.get(reason, reason) if reason is not None else ""
        )
        if reason is not None:
            # The applied schedule is degenerate: show the warning and the
            # confirm checkbox on this render.
            return self._show_degenerate_warning(
                applied, coordinator, program_index, reason
            )
        return self.async_show_form(
            step_id="program_edit",
            data_schema=self._program_schema(
                applied,
                station_names=self._station_names(coordinator),
            ),
            errors={},
            description_placeholders={
                "program": self._program_option_label(coordinator, program_index),
                "preview": self._schedule_preview(applied, coordinator),
                "warning": warning,
            },
        )

    def _show_degenerate_warning(
        self,
        program: IrrigationProgram,
        coordinator: Any,
        program_index: int,
        reason: str,
    ) -> ConfigFlowResult:
        """Re-render the editor with the degenerate-schedule warning.

        Non-blocking (issue #129): shows the submitted values as defaults,
        a schedule preview, the warning, and a confirm checkbox. No write
        happens until the user re-submits with the checkbox set.
        """
        return self.async_show_form(
            step_id="program_edit",
            data_schema=self._program_schema(
                program,
                station_names=self._station_names(coordinator),
                confirm_degenerate=True,
            ),
            errors={},
            description_placeholders={
                "program": self._program_option_label(coordinator, program_index),
                "preview": self._schedule_preview(program, coordinator),
                "warning": _DEGENERATE_WARNINGS.get(reason, reason),
            },
        )

    async def _attempt_write(
        self, program_index: int, program: IrrigationProgram
    ) -> ConfigFlowResult:
        """Write the parsed program to the device, or re-render on failure."""
        coordinator = self._coordinator
        assert coordinator is not None  # caller guarantees a loaded entry
        errors: dict[str, str] = {}
        try:
            await coordinator.set_irrigation_program(program_index, program)
        except Exception:
            _LOGGER.exception(
                "Failed to update Program %s from options flow",
                PROGRAM_LABELS[program_index],
            )
            errors["base"] = "set_program_failed"
            # The two-phase preset cycle did NOT complete: reset the flag so
            # a different preset picked after this failed write is honored
            # instead of being silently ignored (issue #129).
            self._preset_applied = False
        else:
            # Write done: the two-phase preset cycle is complete.
            self._preset_applied = False
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
                "preview": self._schedule_preview(program, coordinator),
                "warning": "",
            },
        )

    @property
    def _coordinator(self) -> Any | None:
        config_entry = self.config_entry
        runtime_data = getattr(config_entry, "runtime_data", None)
        if runtime_data is None:
            return None
        return runtime_data.coordinator

    def _station_name_manager(self) -> StationNameManager | None:
        """Return the coordinator's station-name manager, when loaded."""
        coordinator = self._coordinator
        manager = getattr(coordinator, "station_name_manager", None)
        return manager if isinstance(manager, StationNameManager) else None

    async def async_step_station_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which output to rename, after a fresh snapshot read.

        A pending journal (an earlier save with unknown outcome) blocks
        selection until the user explicitly accepts the current on-device
        names; the journal itself is never replayed.
        """
        coordinator = self._coordinator
        manager = self._station_name_manager()
        if coordinator is None or manager is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None and user_input.get(ATTR_ACCEPT_CURRENT):
            try:
                await manager.refresh(accept_current=True)
            except Exception:
                _LOGGER.exception(
                    "Failed to reconcile pending station-name journal"
                )
                return self.async_abort(reason="station_names_read_failed")
        pending = manager.pending is not None
        if user_input is not None and not pending:
            selected = int(user_input[ATTR_STATION])
            if not 1 <= selected <= coordinator.num_stations:
                return self.async_abort(reason="station_names_read_failed")
            self._selected_station = selected
            return await self.async_step_station_name()
        try:
            await manager.refresh()
        except Exception:
            _LOGGER.exception("Failed to read onboard station names")
            return self.async_abort(reason="station_names_read_failed")
        # The validated snapshot read adopts the device-reported width
        # upward-only inside the library (0.3.2b6); mirror it coordinator-
        # side (issue #122) — adopt_device_station_count is upward-only
        # too, so this can only raise the width, never lower it.
        client_count = getattr(manager.api, "station_count", None)
        coordinator.adopt_device_station_count(
            client_count if isinstance(client_count, int) else None
        )
        pending = manager.pending is not None
        options = [
            {
                "value": str(station),
                "label": manager.snapshot.names.get(station, f"Station {station}")
                if manager.snapshot
                else f"Station {station}",
            }
            for station in range(1, coordinator.num_stations + 1)
        ]
        schema: dict[Any, Any] = {
            vol.Required(ATTR_STATION, default="1"): selector(
                {"select": {"options": options, "mode": "dropdown"}}
            )
        }
        if pending:
            schema[
                vol.Required(ATTR_ACCEPT_CURRENT, default=False)
            ] = selector({"boolean": {}})
        return self.async_show_form(
            step_id="station_select",
            data_schema=vol.Schema(schema),
            errors={"base": "station_name_uncertain"} if pending else None,
        )

    async def async_step_station_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter a new name for the selected output and save it."""
        coordinator = self._coordinator
        manager = self._station_name_manager()
        if coordinator is None or manager is None or manager.snapshot is None:
            return self.async_abort(reason="station_names_read_failed")
        station = getattr(self, "_selected_station", 1)
        errors: dict[str, str] = {}
        draft_name: str | None = None
        if user_input is not None:
            name = str(user_input.get(ATTR_NAME, ""))
            draft_name = name
            try:
                await coordinator.rename_station(
                    station, name, manager.snapshot.revision
                )
            except ValueError:
                errors["base"] = "invalid_station_name"
            except vol.Invalid:
                errors["base"] = "invalid_station_name"
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to rename station %s", station
                )
                errors["base"] = flow_error_for_exception(exc)
                if manager.pending is not None:
                    errors["base"] = "station_name_uncertain"
            else:
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )
        current = draft_name
        if current is None and manager.snapshot is not None:
            current = manager.snapshot.names.get(station, "")
        default = current if current is not None else ""
        return self.async_show_form(
            step_id="station_name",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_NAME, default=default): str,
                }
            ),
            errors=errors,
        )

    def _program_schema(
        self,
        program: IrrigationProgram | None,
        *,
        station_names: dict[int, str] | None = None,
        confirm_degenerate: bool = False,
    ) -> vol.Schema:
        # D1 (issue #122): size the duration fields from the ACTIVE width —
        # the device-derived coordinator width when loaded — not the
        # configured num_stations knob. After a device-derived growth the
        # two diverge, and validation (async_step_program_edit submits with
        # coordinator.num_stations) would expect duration fields the form
        # never rendered, turning every program edit into
        # set_program_failed. Fallback to entry data keeps the editor
        # renderable while the entry is not loaded (runtime_data None).
        coordinator = self._coordinator
        if coordinator is not None:
            num_stations = int(coordinator.num_stations)
        else:
            num_stations = int(
                self.config_entry.data.get(NUM_STATIONS, MIN_NUM_STATIONS)
            )
        defaults = self._program_defaults(program, num_stations=num_stations)
        fields: dict[Any, Any] = {
            vol.Required(
                ATTR_SCHEDULE_PRESET,
                default=_PRESET_NONE,
            ): selector(
                {
                    "select": {
                        "options": [_PRESET_NONE, *_SCHEDULE_PRESETS],
                        "mode": "dropdown",
                        "translation_key": "schedule_preset_selector",
                    }
                }
            ),
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
            # Native time picker (issue #129). The HH:MM string default from
            # _format_minutes serializes fine for a time selector and the
            # parse path accepts both datetime.time (what the picker submits)
            # and legacy "HH:MM" strings. The bare TimeSelector rejects the
            # empty string an untouched/cleared slot submits ("Invalid time
            # specified"), so empty is allowed explicitly: disabled slots are
            # the normal case (most programs use 1-2 of 8 slots). Non-empty
            # values still go through the selector's own validation.
            fields[vol.Optional(key, default=defaults[key])] = vol.Any(
                _START_TIME_EMPTY,
                selector({"time": {}}),
            )
        for station in range(1, num_stations + 1):
            default_key = self._station_key(station)
            key = self._station_duration_key(station, station_names=station_names)
            fields[vol.Required(key, default=defaults[default_key])] = vol.All(
                vol.Coerce(float),
                vol.Range(min=0, max=MAX_PROGRAM_DURATION_MINUTES),
            )
        # Advanced (periodic cycle) fields: rendered LAST so the main flow
        # ends at the station durations. HA options-flow forms have no
        # collapsible sections, so the grouping is purely positional; the
        # step description explains the layout (issue #129).
        fields[vol.Required(
            ATTR_PERIOD_START_DATE,
            default=defaults[ATTR_PERIOD_START_DATE],
        )] = selector({"date": {}})
        fields[vol.Required(
            ATTR_PERIOD_LENGTH,
            default=defaults[ATTR_PERIOD_LENGTH],
        )] = vol.All(vol.Coerce(int), vol.Range(min=1, max=255))
        fields[vol.Required(
            ATTR_SYNCHRO_DAY,
            default=defaults[ATTR_SYNCHRO_DAY],
        )] = vol.All(vol.Coerce(int), vol.Range(min=0, max=255))
        if confirm_degenerate:
            fields[vol.Required(CONFIRM_DEGENERATE, default=False)] = selector(
                {"boolean": {}}
            )
        return vol.Schema(fields)

    def _schedule_preview(
        self,
        program: IrrigationProgram | None,
        coordinator: Any | None,
    ) -> str:
        """Human schedule summary for the form description.

        Purely informational: any failure yields an empty string so the
        preview can never block or crash a write.
        """
        if program is None:
            return ""
        try:
            summary = schedule_summary(program, self._station_names(coordinator))
            nxt = next_start_datetime(program, dt_util.now())
            nxt_text = (
                dt_util.as_local(nxt).strftime("%a %H:%M") if nxt else "none"
            )
            total = sum(d for d in program["station_durations"] if d > 0)
            return (
                f"{summary or 'no start times'}"
                f" · next start {nxt_text} · {format_duration(total)}/run"
            )
        except Exception:  # noqa: BLE001 - preview must never block a write
            _LOGGER.debug("Schedule preview rendering failed", exc_info=True)
            return ""

    def _best_effort_preview_program(
        self,
        user_input: dict[str, Any],
        coordinator: Any,
        program_index: int,
    ) -> IrrigationProgram | None:
        """Parse submitted input leniently for the preview on error paths.

        Returns ``None`` when nothing usable can be salvaged; used only for
        rendering, never for validation or writes. Negative station
        durations (rejected by strict validation) are clamped to zero so
        the schedule preview still shows the rest of the config.
        """
        data = dict(user_input)
        for station in range(1, int(coordinator.num_stations) + 1):
            station_names = self._station_names(coordinator)
            for key in (
                self._station_key(station),
                self._station_duration_key(station, station_names=station_names),
            ):
                try:
                    value = data.get(key)
                    if value is not None and float(value) < 0:
                        data[key] = 0
                except (TypeError, ValueError):
                    # One non-numeric value must not abort clamping of the
                    # remaining stations.
                    continue
        try:
            return self._program_from_options_input(
                data,
                num_stations=coordinator.num_stations,
                station_names=self._station_names(coordinator),
                current_program=coordinator.irrigation_programs.get(program_index),
            )
        except Exception:  # noqa: BLE001 - preview must never block a render
            return None

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
        current_program: IrrigationProgram | None = None,
    ) -> IrrigationProgram:
        return _parse_program_input(
            data,
            num_stations=num_stations,
            station_names=station_names,
            current_program=current_program,
        )

    @staticmethod
    def _apply_preset(
        preset: str,
        form_input: dict[str, Any],
        *,
        num_stations: int | None = None,
        station_names: dict[int, str] | None = None,
    ) -> IrrigationProgram:
        """Apply a schedule preset to a parsed program-editor input.

        Pure function: builds the program via ``_program_from_options_input``
        (which derives ``synchro_day`` when the anchor date changed) and, for
        a non-``none`` preset, overlays the preset's encoding on the result.
        The anchored presets keep the form's ``period_start_date`` as their
        anchor; ``every_day`` resets the periodic fields to weekly semantics.
        When ``num_stations`` is not given, it is inferred from the duration
        fields present in the input (plain or station-name-labelled).
        """
        if num_stations is None:
            num_stations = sum(
                1
                for key in form_input
                if str(key).endswith("_duration")
                or ("(station " in str(key) and "duration (minutes)" in str(key))
            )
            num_stations = max(num_stations, 1)
        program = _parse_program_input(
            form_input,
            num_stations=num_stations,
            station_names=station_names,
        )
        if preset == _PRESET_NONE:
            return program
        program.update(_SCHEDULE_PRESETS[preset])  # type: ignore[typeddict-item]
        if program["cycle"] == 4 and program["period_length"] > 1:
            # The overlay changed the period length: renormalize the parsed
            # phase into the new period so the anchor stays meaningful.
            program["synchro_day"] %= program["period_length"]
        return program

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
        if isinstance(value, time):
            # Native time selector submits a datetime.time (possibly with
            # seconds); the device stores minutes since midnight.
            return value.hour * 60 + value.minute
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


def _parse_program_input(
    data: dict[str, Any],
    *,
    num_stations: int,
    station_names: dict[int, str] | None = None,
    current_program: IrrigationProgram | None = None,
) -> IrrigationProgram:
    """Parse program-editor form input into an IrrigationProgram.

    Module-level so the pure preset applier can reuse it without an
    instance. Derives ``synchro_day`` when the anchor date changed.
    """
    start_times = [
        SolemOptionsFlowHandler._parse_optional_time(
            data.get(SolemOptionsFlowHandler._start_key(slot), "")
        )
        for slot in range(8)
    ]
    period_start_date = data[ATTR_PERIOD_START_DATE]
    if isinstance(period_start_date, str):
        period_start_date = date.fromisoformat(period_start_date)
    previous_period_start_date = (
        current_program.get("period_start_date")
        if current_program is not None
        else None
    )
    period_length = int(data[ATTR_PERIOD_LENGTH])
    synchro_day = int(data[ATTR_SYNCHRO_DAY])
    if period_start_date != previous_period_start_date:
        synchro_day = (
            (period_start_date - previous_period_start_date).days % period_length
            if previous_period_start_date is not None
            else 0
        )
    return {
        "name": str(data[ATTR_NAME]),
        "inter_station_delay": int(data[ATTR_INTER_STATION_DELAY]),
        "water_budget": int(data[ATTR_WATER_BUDGET]),
        "cycle": _CYCLES[str(data[ATTR_CYCLE])],
        "week_days": SolemOptionsFlowHandler._weekdays_mask(
            list(data[ATTR_WEEK_DAYS])
        ),
        "period_length": period_length,
        "synchro_day": synchro_day,
        "period_start_date": period_start_date,
        "start_times": start_times,
        "station_durations": [
            SolemOptionsFlowHandler._duration_seconds(
                SolemOptionsFlowHandler._station_duration_value(
                    data,
                    station,
                    station_names=station_names,
                )
            )
            for station in range(1, num_stations + 1)
        ],
    }


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class CannotConnectSlots(CannotConnect):
    """Error to indicate Bluetooth adapters/proxies are out of connection slots."""
