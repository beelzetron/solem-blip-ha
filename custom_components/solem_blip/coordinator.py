"""DataUpdateCoordinator for the Solem BL-IP integration."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any, cast

from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import Context, HomeAssistant

from homeassistant.helpers import issue_registry as ir

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from solem_blip_ble import IrrigationProgram
from solem_blip_ble.exceptions import InvalidSnapshot, UncertainWrite

from .client_factory import (
    PersistentSolemClient,
    build_solem_client,
)

from .config_entry import MyConfigEntry
from .const import (
    BLUETOOTH_DEFAULT_TIMEOUT,
    BLUETOOTH_TIMEOUT,
    CONTROLLER_MAC_ADDRESS,
    CONTROLLER_NAME,
    DEFAULT_CONTROLLER_OFF_DAYS,
    DEFAULT_MANUAL_DURATION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IRRIGATION_CONFIG_UPDATE_INTERVAL,
    NUM_STATIONS,
    PERSISTENT_CONNECTION,
    PERSISTENT_DISCONNECT_TIMEOUT,
    PROGRAM_LABELS,
    SOLEM_API_MOCK,
)
from .coordinator_descriptors import build_all_descriptors
from .issues import ISSUE_STATION_COUNT_MISMATCH
from .coordinator_irrigation import (
    await_irrigation_monitor_task,
    clear_irrigation_idle_state,
    clear_monitor_task_ref,
    run_irrigation_monitor,
    start_irrigation as irrigation_start,
    start_program as irrigation_start_program,
    stop_irrigation as irrigation_stop,
    turn_controller_off as irrigation_turn_off,
    turn_controller_off_for_days as irrigation_turn_off_for_days,
    turn_controller_on as irrigation_turn_on,
)
from .coordinator_polling import (
    apply_status,
    fetch_device_metadata,
    fetch_device_status,
    fetch_irrigation_config,
    remaining_minutes_for_station,
)
from .coordinator_publish import publish_descriptor_update
from .display_names import DisplayNamesStore
from .bluetooth import async_get_connectable_device

from .models import IrrigationController, IrrigationStation
from .program_backup import ProgramBackupStore
from .station_names import StationNameManager
from .activity import WateringActivity
from .ble_health import note_cycle_outcome
from .bluetooth_issue import note_ble_recovery
from .stuck_recovery import (
    attach_stuck_adapter_detector,
    detach_stuck_adapter_detector,
)

_LOGGER = logging.getLogger(__name__)


class SolemCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Poll BLE status and expose manual irrigation controls."""

    def __init__(self, hass: HomeAssistant, config_entry: MyConfigEntry) -> None:
        self.controller_mac_address = config_entry.data[CONTROLLER_MAC_ADDRESS].rsplit(
            " - ", 1
        )[1]
        _LOGGER.info(
            "%s - Starting coordinator initialization...",
            self.controller_mac_address,
        )

        self.poll_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self.bluetooth_timeout = config_entry.options.get(
            BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT
        )
        self.solem_api_mock = (
            config_entry.options.get(SOLEM_API_MOCK, "false") == "true"
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
            config_entry=config_entry,
            always_update=True,
        )

        self._config_num_stations = int(config_entry.data.get(NUM_STATIONS, 2))
        # Device-derived station width (issue #122), UPWARD-ONLY. The
        # device always reports every configured slot in a name read (real
        # captures: 12 slots at every physical width), so the highest named
        # output is a LOWER bound on the physical width, never an upper
        # bound: stations can exist physically but carry no onboard name
        # (fresh installs keep their names HA-side). The count therefore
        # starts at the configured num_stations — the config knob is a
        # trustworthy floor — and a name read can only RAISE it (a rename
        # naming a higher slot proves that output exists). Adopting a
        # narrower derived count would drop live status for the unnamed
        # higher stations (apply_status bounds-checks by num_stations) —
        # the same defect the library fixed upward-only in 0.3.2b6.
        self.device_station_count: int = self._config_num_stations
        self._device_count_notice_created = False
        # Constructor width the BLE client was built with; a client
        # station_count equal to this has not been refreshed by a validated
        # snapshot read yet, so it carries no device information.
        self._client_initial_station_count = self._config_num_stations
        self.config_entry = config_entry
        self.entity_translations: dict[str, Any] = {}

        self.controller = IrrigationController(
            device_id=f"{self.controller_mac_address}_irrigation_controller_status",
            device_name="Controller Status",
            device_uid="",
            software_version=None,
        )
        self.station_names: dict[int, str] = {}
        self.firmware_version: str | None = None
        # Confirmed onboard name from the identification record. Restored
        # from entry data so offline startup labels correctly; a fresh
        # read overwrites it via controller_name.apply_controller_name.
        self.controller_name: str | None = config_entry.data.get(CONTROLLER_NAME)
        self._firmware_retry_after = 0.0
        self._station_names_retry_after = 0.0
        self.stations = self._build_stations()

        self.api = build_solem_client(
            config_entry,
            mac_address=self.controller_mac_address,
            bluetooth_timeout=self.bluetooth_timeout,
            mock=self.solem_api_mock,
            max_station_num=self.num_stations,
            ble_device_resolver=lambda: async_get_connectable_device(
                hass, self.controller_mac_address
            ),
        )
        self.persistent_connection = isinstance(self.api, PersistentSolemClient)

        self.irrigation_stop_event = asyncio.Event()
        self._irrigation_active = False
        self._irrigation_monitor_task: asyncio.Task[None] | None = None
        self._ready = False
        self.battery_voltage: int | None = None
        self.battery_level: int | None = None
        self.battery_low: bool | None = None
        self.time_alarm: bool | None = None
        self._has_status = False
        self.irrigation_manual_duration = DEFAULT_MANUAL_DURATION
        self.controller_off_days = DEFAULT_CONTROLLER_OFF_DAYS
        self.controller_off_mode = "unknown"
        self.controller_off_days_remaining: int | None = None
        self.remaining_seconds: int | None = None
        self.active_station_num: int | None = None
        self.active_program_num: int | None = None
        self.watering_origin: str | None = None
        self.irrigation_programs: dict[int, IrrigationProgram] = {}
        self.program_backup = ProgramBackupStore(hass, config_entry.entry_id)
        self.station_name_manager = StationNameManager(
            hass, config_entry.entry_id, self.api
        )
        self.display_names = DisplayNamesStore(hass, config_entry.entry_id)
        # Last successfully observed program display names; consulted only
        # when the live irrigation-config read has not produced a name.
        self.program_names: dict[int, str] = {}
        self._irrigation_config_retry_after = 0.0
        self._irrigation_config_refresh_after = 0.0
        self.schedule_coordinator = SolemScheduleCoordinator(hass, config_entry, self)
        self._last_set_time_at = 0.0
        self._time_sync_retry_after = 0.0
        self._last_set_time_sync: datetime | None = None
        self._set_time_pending = True
        self._ble_cycle_degraded_streak = 0
        self._ble_health_events: deque[float] = deque()
        self._ble_issue_active = False
        self._ble_first_healthy_at: float | None = None
        self._last_successful_poll_at: float | None = None
        self._is_watering = False
        self._metadata_task: asyncio.Task[None] | None = None
        self._station_names_restored = False
        self._heavy_read_lock = asyncio.Lock()
        self._first_successful_status_at: float | None = None
        self._metadata_ready_after = float("inf")
        self._schedule_ready_after = float("inf")
        self._schedule_gate = asyncio.Event()
        self.activity = WateringActivity(self)
        self.stuck_adapter_detector = attach_stuck_adapter_detector(self)

        _LOGGER.info(
            "%s - Coordinator initialization finished.",
            self.controller_mac_address,
        )

    @property
    def num_stations(self) -> int:
        """Active station width: device-derived floor, config knob at start.

        The width starts at the user-configured ``num_stations`` entry data
        and is then adopted upward-only from device name reads (#122, see
        ``maybe_adopt_device_station_count``): a name read can raise the
        count but never lower it.
        """
        return self.device_station_count

    def adopt_device_station_count(self, count: int | None) -> bool:
        """Adopt a device-reported station count, upward-only (issue #122).

        Mirrors the library invariant (solem-blip-ble 0.3.2b6): the device
        always reports every configured slot in name reads, so the highest
        named output is a lower bound on the physical width — a read can
        RAISE the count but never lower it. Shrinking to a narrower derived
        count would drop live status for unnamed higher stations.

        Returns True when the count grew and a repair notice was created.
        ``None`` means the client does not expose a device-derived count
        (e.g. mock mode): the current floor stays in effect.
        """
        if count is None or count < 1:
            return False
        if count <= self.device_station_count:
            return False
        previous = self.device_station_count
        self.device_station_count = count
        _LOGGER.info(
            "%s - Device reports %d station(s); station width raised from %d "
            "(upward-only adoption)",
            self.controller_mac_address,
            count,
            previous,
        )
        # Keep the in-memory station models in sync with the wider width:
        # stations are first built from the config value at setup (entities
        # must exist before BLE connects), so growth appends fresh models.
        # NOTE (issue #122): the appended models do NOT gain valve/button/
        # sensor entities immediately — entities are created once per
        # platform setup from coordinator.data, and publish_descriptor_update
        # never re-invokes async_add_entities. Adopted stations become
        # controllable after the entry is reloaded (or after an HA
        # restart); the repair notice below directs the user to update
        # num_stations via reconfigure, which reloads the entry and
        # rebuilds entities. Dynamic entity creation is deliberately out
        # of scope for #122.
        self.stations = self._build_stations()
        self._create_station_count_mismatch_issue(previous)
        return True

    def _create_station_count_mismatch_issue(self, configured: int) -> None:
        """Create a repair notice when the device proved a wider width.

        Raised once per entry per session and only when the user explicitly
        configured ``num_stations`` (not when running on the default). With
        upward-only adoption the notice means the configured value is
        stale/too low: the device proved more stations than configured.
        """
        if self._device_count_notice_created:
            return
        entry = self.config_entry
        if entry is None or NUM_STATIONS not in entry.data:
            return
        self._device_count_notice_created = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_STATION_COUNT_MISMATCH}_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_STATION_COUNT_MISMATCH,
            translation_placeholders={
                "configured": str(configured),
                "reported": str(self.device_station_count),
            },
        )

    def _station_name(self, station_id: int) -> str:
        """Return the controller-provided station name or a stable fallback."""
        return self.station_names.get(station_id) or f"Station {station_id}"

    def _program_display_name(self, program_index: int) -> str:
        """Return the on-device program name or a stable slot fallback.

        Resolution order: the live irrigation-config read, the cached
        last-observed name restored across restarts, then the slot label.
        """
        program = self.irrigation_programs.get(program_index)
        if program and (name := program.get("name", "").strip()):
            return name
        cached = self.program_names.get(program_index)
        if cached:
            return cached
        return f"Program {PROGRAM_LABELS[program_index]}"

    def _build_stations(self) -> list[IrrigationStation]:
        """Build station models for the configured station count."""
        return [
            IrrigationStation(
                device_id=f"{self.controller_mac_address}_irrigation_station_{station_id}_status",
                device_name=f"{self._station_name(station_id)} Status",
                device_uid="",
                station_number=station_id,
                software_version=self.firmware_version,
            )
            for station_id in range(1, self.num_stations + 1)
        ]

    async def async_shutdown(self) -> None:
        """Tear down coordinator tasks when the integration unloads.

        The irrigation monitor task is cancelled first so no in-flight
        operation races the teardown. With ``persistent_connection`` enabled
        the client holds the BLE link between polls, so it is explicitly
        disconnected here (bounded, so a resisting controller cannot stall
        unload); the stateless v2 client holds no BLE session between
        operations and closes its own connection when the monitor task is
        cancelled.
        """
        self.irrigation_stop_event.set()
        task = self._irrigation_monitor_task
        if task is not None and not task.done():
            task.cancel()
        await self._await_irrigation_monitor_task()
        if self.persistent_connection:
            try:
                async with asyncio.timeout(PERSISTENT_DISCONNECT_TIMEOUT):
                    await cast("PersistentSolemClient", self.api).disconnect()
            except TimeoutError:
                _LOGGER.warning(
                    "%s - Persistent BLE disconnect timed out during shutdown; "
                    "continuing teardown",
                    self.controller_mac_address,
                )
        self._clear_irrigation_idle_state()
        await self.schedule_coordinator.async_shutdown()
        await self.activity.shutdown()
        detach_stuck_adapter_detector(self.stuck_adapter_detector)

    def request_schedule_refresh(self) -> None:
        """Mark schedule data due for the next slow-coordinator refresh."""
        self._irrigation_config_refresh_after = 0.0

    async def refresh_irrigation_programs(self) -> None:
        """Force a fresh program read and surface BLE failures to the caller."""
        await fetch_irrigation_config(self, force=True, raise_on_error=True)
        self.schedule_coordinator.async_set_updated_data(self.irrigation_programs)
        publish_descriptor_update(
            self,
            await self.async_update_all_sensors(fetch_status=False),
        )

    async def async_init(self) -> None:
        """Build initial entity data without blocking setup on BLE availability."""
        await self.program_backup.async_load()
        await self.station_name_manager.async_load()
        await self.activity.load()
        # Seed the display-name caches with the last successfully observed
        # onboard names so a restart (including an offline one) shows no
        # "Station N"/"Program X" window. The device remains the source of
        # truth: the first successful metadata read refreshes the cache,
        # picking up renames made outside this integration. Descriptor
        # rebuild and publish are intentionally skipped here: setup builds
        # and renders entities right after async_init.
        await self.display_names.async_load()
        if restored_names := dict(self.display_names.station_names):
            self.station_names.update(
                {
                    station_id: name
                    for station_id, name in restored_names.items()
                    if 1 <= station_id <= self.num_stations
                }
            )
            self._station_names_restored = True
            # The status models carry a snapshot of the name taken in
            # __init__, before the restore; rebuild it from the cache so
            # the "X Status" device names also skip the default window
            # (issue #118).
            for station_model in self.stations:
                station_model.device_name = (
                    f"{self._station_name(station_model.station_number)} Status"
                )
        if restored_programs := dict(self.display_names.program_names):
            self.program_names.update(restored_programs)
        self._ready = True
        self.data = await self.async_update_all_sensors(fetch_status=False)
        self.last_update_success = False

    def _apply_status(self, status: dict[str, Any]) -> None:
        """Update coordinator state from a BLE status dict."""
        apply_status(self, status)

    async def _fetch_device_status(self) -> dict[str, Any]:
        """Poll device and update controller/station states from BLE status."""
        return await fetch_device_status(self)

    async def _fetch_device_metadata(self) -> None:
        """Read firmware and station names without failing status polling."""
        await fetch_device_metadata(self)

    def _clear_irrigation_idle_state(self) -> None:
        """Reset coordinator state after irrigation stops or fails to start."""
        clear_irrigation_idle_state(self)

    def _clear_monitor_task_ref(self, task: asyncio.Task[None]) -> None:
        """Clear stored monitor task when it completes."""
        clear_monitor_task_ref(self, task)

    def request_device_time_sync(self) -> None:
        """Force a device-time sync on the next successful status poll.

        Clears the 24h throttle set by ``maybe_set_device_time`` so a
        controller that lost its clock (e.g. after a power/battery blip)
        is re-synced as soon as the BLE link recovers, instead of waiting
        up to a day.
        """
        self._set_time_pending = True

    async def _await_irrigation_monitor_task(self) -> None:
        """Wait for the background irrigation monitor to finish."""
        await await_irrigation_monitor_task(self)

    def _remaining_minutes_for_station(self, station_id: int) -> int | None:
        """Return remaining sprinkle minutes for a station (0 when idle/inactive)."""
        return remaining_minutes_for_station(self, station_id)

    async def async_update_all_sensors(
        self, *, fetch_status: bool = True
    ) -> list[dict[str, Any]]:
        """Build entity descriptor list from current coordinator state."""
        if fetch_status:
            await self._fetch_device_status()
        return build_all_descriptors(self)

    async def async_update_data(self) -> list[dict[str, Any]]:
        try:
            data = await self.async_update_all_sensors()
            self._last_successful_poll_at = asyncio.get_running_loop().time()
            note_ble_recovery(self)
            note_cycle_outcome(self, degraded=False, reason="")
            _LOGGER.debug(
                "%s - Status poll completed",
                self.controller_mac_address,
            )
            return data
        except Exception as err:
            note_cycle_outcome(self, degraded=True, reason="status poll failed")
            self.stuck_adapter_detector.note_cycle_outcome(degraded=True)
            raise UpdateFailed(f"Failed to update BLE status: {err}") from err

    async def start_irrigation(
        self,
        station: int,
        minutes: int | None = None,
        context: Context | None = None,
    ) -> None:
        """Send a start command, then monitor watering in the background."""
        async with self.activity.command(
            station=station, context=context
        ):
            await irrigation_start(self, station, minutes)

    async def start_program(
        self, program_num: int, context: Context | None = None
    ) -> None:
        """Start one on-device irrigation program."""
        async with self.activity.command(
            program=program_num, context=context
        ):
            await irrigation_start_program(self, program_num)

    async def _run_irrigation_monitor(self, station: int, duration: int) -> None:
        """Monitor active watering until completion, stop, or safety timeout."""
        await run_irrigation_monitor(self, station, duration)

    async def stop_irrigation(self) -> None:
        """Stop active manual watering."""
        await irrigation_stop(self)

    async def set_irrigation_program(
        self,
        program_index: int,
        program: IrrigationProgram,
    ) -> None:
        """Write one on-device irrigation program and refresh schedule data."""
        self.irrigation_programs = await self.api.set_irrigation_program(
            program_index,
            program,
        )
        self.request_schedule_refresh()
        self.async_set_updated_data(await self.async_update_all_sensors(fetch_status=False))
        self.schedule_coordinator.async_set_updated_data(self.irrigation_programs)
        await self.program_backup.async_save_if_non_empty(self.irrigation_programs)
        # The program editor's write returned the observed post-write state:
        # refresh the program-name cache in the same success path so a
        # restart cannot resurrect the pre-rename name (issue #118).
        await self.display_names.async_save(
            program_names={
                index: str(program.get("name", "")).strip()
                for index, program in self.irrigation_programs.items()
                if str(program.get("name", "")).strip()
            }
        )

    def program_mutation_blocked(self) -> bool:
        """Return whether local state says irrigation is currently active."""
        return self._irrigation_active or self._is_watering

    async def rename_station(
        self, station: int, name: str, revision: str
    ) -> None:
        """Rename one onboard output through the safety-checked manager.

        Runs under the heavy-read lock so the idle preflight and the
        write cannot interleave with a metadata read or a program
        operation touching the same connection. On success the
        coordinator's cached station labels are refreshed from the
        verified snapshot so entity names follow immediately.
        """
        async with self._heavy_read_lock:
            snapshot = await self.station_name_manager.update(
                station, name, revision
            )
        # The validated snapshot read/write path adopts the device-reported
        # width upward-only inside the library (0.3.2b6); mirror it
        # coordinator-side so entity models follow a wider count that the
        # filtered dict reads cannot express (issue #122). Upward-only on
        # both sides: the library floor + the coordinator floor compose via
        # max, so a mirrored count can only grow the width here too.
        client_count = getattr(self.api, "station_count", None)
        self.adopt_device_station_count(
            client_count if isinstance(client_count, int) else None
        )
        self.station_names.update(
            {
                station_id: name_text.strip() or f"Station {station_id}"
                for station_id, name_text in snapshot.names.items()
                if 1 <= station_id <= self.num_stations
            }
        )
        # The editor's readback just verified the onboard names: refresh the
        # cache from that snapshot in the same success path so a restart
        # cannot resurrect the pre-rename name for a full read cycle.
        await self.display_names.async_save(
            station_names=dict(self.station_names)
        )
        for station_model in self.stations:
            station_model.device_name = (
                f"{self._station_name(station_model.station_number)} Status"
            )
        publish_descriptor_update(
            self, await self.async_update_all_sensors(fetch_status=False)
        )

    async def update_protected_program_backup(self) -> None:
        """Replace the protected restore point after explicit user action only."""
        if self.program_mutation_blocked():
            raise InvalidSnapshot(
                "Controller must be idle before updating the protected backup"
            )
        if self.program_backup.pending is not None:
            raise InvalidSnapshot(
                "Cannot update the protected backup while a restore is pending"
            )

        async with self._heavy_read_lock:
            # Re-check the controller itself immediately before the heavy read;
            # cached HA state alone is not sufficient for a destructive backup
            # replacement decision.
            status = await self.api.get_status()
            if (
                status.get("is_watering") is not False
                or status.get("controller_state") not in ("On", "Off")
            ):
                raise InvalidSnapshot(
                    "Controller must report idle before updating the protected backup"
                )
            firmware = await self.api.get_firmware_version()
            if firmware["major"] != 5:
                raise InvalidSnapshot(
                    "Only original BL-IP firmware 5.x program backups are supported"
                )

            snapshot = await self.api.get_program_snapshot()
            # async_replace reparses and validates all raw frames before it
            # changes the durable restore point.
            await self.program_backup.async_replace(snapshot)

        self.irrigation_programs = {
            index: self.program_backup.programs[index]
            for index in (0, 1, 2)
            if index in self.program_backup.programs
        }
        self.program_backup.last_read = datetime.now().astimezone().isoformat()
        self.schedule_coordinator.async_set_updated_data(self.irrigation_programs)
        self.async_set_updated_data(
            await self.async_update_all_sensors(fetch_status=False)
        )

    async def restore_irrigation_programs(self) -> None:
        """Restore protected A/B/C programs in one acknowledged transaction."""
        if self.program_mutation_blocked():
            raise InvalidSnapshot(
                "Controller must be idle before restoring programs"
            )

        programs = self.program_backup.programs
        if not programs:
            raise ValueError("No irrigation program backup is available")
        pending_restore = self.program_backup.pending

        status = await self.api.get_status()
        if (
            status.get("is_watering") is not False
            or status.get("controller_state") not in ("On", "Off")
        ):
            raise InvalidSnapshot(
                "Controller must report idle before restoring programs"
            )
        firmware = await self.api.get_firmware_version()
        if firmware["major"] != 5:
            raise InvalidSnapshot(
                "Only original BL-IP firmware 5.x program restores are supported"
            )

        # Start from a fresh complete snapshot. Legacy beta.1-beta.11 backups
        # contain A/B/C only, so the nine additional V5 slots are preserved
        # byte-for-byte from the controller and are never written.
        before = await self.api.get_program_snapshot()
        if pending_restore:
            # A previous uncertain write may have left the controller partially
            # mutated. A fresh complete snapshot is authoritative for the retry:
            # first reconcile a known outcome, otherwise explicitly resume from
            # the observed partial state. The new before/expected journal below
            # replaces the old one before any further mutation.
            await self.program_backup.async_reconcile(before)

        expected = before
        frames: list[bytes] = []
        for program_index, program in sorted(programs.items()):
            changes: dict[str, Any] = {
                "name": program["name"],
                "inter_station_delay": program["inter_station_delay"],
                "water_budget": program["water_budget"],
                "cycle": program["cycle"],
                "week_days": program["week_days"],
                "period_length": program["period_length"],
                "synchro_day": program["synchro_day"],
                "start_times": list(program["start_times"]),
                "station_durations": {
                    station: seconds
                    for station, seconds in enumerate(
                        program["station_durations"], start=1
                    )
                    if station <= self.num_stations
                },
            }
            # period_start_date is controller-owned restore metadata.
            # Real BL-IP hardware normalizes/retains the fresh controller date
            # after a write, so replaying the backup date makes an otherwise
            # successful restore fail the byte-for-byte revision check.
            program_frames, expected = expected.patch(
                program_index,
                changes,
                self.num_stations,
            )
            frames.extend(program_frames)

        if not frames:
            await self.program_backup.async_finish_restore(before)
            return

        # Persist the intended before/after revisions before the first mutation.
        await self.program_backup.async_begin_restore(before, expected)
        try:
            verified = await self.api.write_program_frames(
                frames,
                expected,
                before.revision,
            )
        except UncertainWrite:
            _LOGGER.warning(
                "%s - Program restore left unconfirmed (%s)",
                self.controller_mac_address,
                getattr(self.api, "program_write_diagnostics", {}),
            )
            raise
        except Exception:
            # The BLE library only returns a non-UncertainWrite failure when
            # no program mutation was attempted (for example a preflight link
            # drop or stale revision). The durable journal can therefore be
            # cleared without touching the protected backup.
            await self.program_backup.async_abort_restore()
            _LOGGER.warning(
                "%s - Program restore aborted before mutation (%s)",
                self.controller_mac_address,
                getattr(self.api, "program_write_diagnostics", {}),
            )
            raise

        await self.program_backup.async_finish_restore(verified)
        self.irrigation_programs = {
            index: verified.programs[index]
            for index in (0, 1, 2)
            if index in verified.programs
        }
        self.schedule_coordinator.async_set_updated_data(self.irrigation_programs)
        self.async_set_updated_data(
            await self.async_update_all_sensors(fetch_status=False)
        )

    async def turn_controller_on(self) -> None:
        """Turn the irrigation controller on."""
        await irrigation_turn_on(self)

    async def turn_controller_off(self) -> None:
        """Turn the irrigation controller off permanently."""
        await irrigation_turn_off(self)

    async def turn_controller_off_for_days(self) -> None:
        """Turn the irrigation controller off for the configured number of days."""
        await irrigation_turn_off_for_days(self)

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Return one entity descriptor from coordinator data."""
        if not self.data:
            return None
        for device in self.data:
            if device["device_id"] == device_id:
                return device
        return None

    def get_device_parameter(self, device_id: str, parameter: str) -> Any:
        """Return one field from an entity descriptor."""
        if device := self.get_device(device_id):
            return device.get(parameter)
        return None


class SolemScheduleCoordinator(DataUpdateCoordinator[dict[int, IrrigationProgram]]):
    """Refresh persisted irrigation schedules without delaying status polls."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyConfigEntry,
        coordinator: SolemCoordinator,
    ) -> None:
        self.solem_coordinator = coordinator
        self._first_refresh_started = False
        self._config_entry = config_entry
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} schedule ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=IRRIGATION_CONFIG_UPDATE_INTERVAL),
            config_entry=config_entry,
            always_update=False,
        )

    def async_start_first_refresh(self) -> None:
        """Start the first schedule refresh after schedule entities subscribe."""
        if self._first_refresh_started:
            return
        self._first_refresh_started = True
        self._config_entry.async_create_background_task(
            self.hass,
            self._async_deferred_first_refresh(),
            name=f"{DOMAIN} schedule first refresh",
        )

    async def _async_deferred_first_refresh(self) -> None:
        """Wait for the heavy-read gate before the first irrigation config read."""
        coordinator = self.solem_coordinator
        await coordinator._schedule_gate.wait()
        remaining = coordinator._schedule_ready_after - (
            asyncio.get_running_loop().time()
        )
        if remaining > 0:
            await asyncio.sleep(remaining)
        _LOGGER.debug(
            "%s - Schedule coordinator starting first refresh",
            coordinator.controller_mac_address,
        )
        await coordinator._fetch_device_metadata()
        await self.async_refresh()

    async def async_update_data(self) -> dict[int, IrrigationProgram]:
        """Refresh schedule state and publish updated program descriptors."""
        await fetch_irrigation_config(self.solem_coordinator)
        publish_descriptor_update(
            self.solem_coordinator,
            await self.solem_coordinator.async_update_all_sensors(fetch_status=False),
        )
        return self.solem_coordinator.irrigation_programs
