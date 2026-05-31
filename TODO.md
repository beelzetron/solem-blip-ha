# Solem BL-IP Home Assistant Integration — Enhancement Plan

Recommended improvements to align with Home Assistant best practices. Status reviewed against the codebase on 2026-05-30; Phase 1 completed 2026-05-30.

---

## Priority Legend

| Priority | When | Impact |
|----------|------|--------|
| 🔴 High | Next release | Stability / UX |
| 🟡 Medium | When refactoring | Maintainability |
| 🟢 Low | Backlog | Nice to have |

---

## 🔴 High Priority

### 1. Fix Button Error Handling

**Status:** Done  
**Files:** `button.py`, `coordinator.py`

**Implemented:**
- Buttons await coordinator commands and raise `HomeAssistantError` on `APIConnectionError`
- Coordinator command methods re-raise after logging (stop, turn on/off)
- `start_irrigation()` sets `_irrigation_active` and optimistic station state before the BLE command; resets idle state on failure
- `start_irrigation()` stores the monitor task on the coordinator; done callback clears the reference with identity check
- Guard against overlapping irrigation starts (`_irrigation_active`)
- `stop_irrigation()` cooperatively stops via event and awaits the monitor task
- `async_shutdown()` cancels and awaits the monitor task before BLE disconnect

**Remaining:**
- [ ] Test disconnect / timeout scenarios on hardware or with mock API

---

### 2. Add Entity Categories for Diagnostic Sensors

**Status:** Done  
**Files:** `sensor.py`, `binary_sensor.py`

- [x] Buttons use `EntityCategory.CONFIG` (`button.py`)
- [x] Number entity uses `EntityCategory.CONFIG` (`number.py`)
- [x] Battery voltage disabled by default (`_attr_entity_registry_enabled_default = False`)
- [x] `EntityCategory.DIAGNOSTIC` on `BatterySensor`, `BatteryVoltageSensor`, `BatteryLow`

---

### 3. Finish Configuration Flow Validation

**Status:** Done  
**Files:** `config_flow.py`, `const.py`

- [x] `num_stations` minimum (1) and maximum (8)
- [x] `scan_interval` minimum (10s) and maximum (3600s)
- [x] `bluetooth_timeout` minimum (5s) and maximum (300s)
- [x] Coordinator passes `max_station_num=self.num_stations` to `SolemClient`

---

### 4. BLE Cleanup on Unload

**Status:** Done  
**Files:** `__init__.py`, `coordinator.py`

- [x] `SolemCoordinator.async_shutdown()` calls `api.disconnect()`
- [x] `async_shutdown()` cancels and awaits the irrigation monitor task before disconnect
- [x] `async_unload_entry()` shuts down coordinator and removes `hass.data` entry

---

## 🟡 Medium Priority

### 5. Add Translation Keys to All Entities

**Status:** Not started  
**Files:** `sensor.py`, `button.py`, `number.py`, `binary_sensor.py`, `base.py`, `translations/en.json`, `translations/it.json`

**Migration note:** Adding `translation_key` may change displayed entity names. `unique_id` values are stable.

**Tasks:**
- [ ] Add `_attr_translation_key` to all entity classes
- [ ] Add `entity` section to `translations/en.json` and `translations/it.json`
- [ ] Stop using `device["device_name"]` for entity naming
- [ ] Consider `SensorDeviceClass.ENUM` with options for controller and station state sensors

---

### 6. Convert to Entity Description Dataclasses

**Status:** Not started (intermediate pattern exists)  
**Files:** `sensor.py`, `button.py`, `number.py`, `binary_sensor.py`, `coordinator.py`

**Tasks:**
- [ ] Create `SolemSensorDescription`, `SolemButtonDescription`, etc.
- [ ] Define static description tuples for controller-level entities
- [ ] Instantiate station-scoped entities in a loop over `coordinator.num_stations`
- [ ] Slim down or remove the entity-descriptor list from `async_update_all_sensors()`

---

### 7. Improve Device Registry Information

**Status:** Blocked on protocol  
**Files:** `base.py`, `coordinator.py`, `solem-blip-ble` (upstream)

**Problem:** `device_info` hardcodes `sw_version="1.0"`. `solem-blip-ble` has `pack_get_firmware_version()` / `parse_firmware_version_response()` helpers and tests, but no `SolemClient.get_firmware_version()` yet and the V5 wire format needs hardware validation.

**Tasks:**
- [ ] Add `get_firmware_version()` to `SolemClient` (validate against hardware first)
- [ ] Query once at coordinator init; store on coordinator
- [ ] Update `DeviceInfo` in `base.py` with dynamic `sw_version` / `hw_version`

---

## 🟢 Low Priority

### 8. Integration Metadata and Repo Hygiene

**Status:** Mostly done  
**Remaining:**
- [ ] GitHub issue templates (`.github/ISSUE_TEMPLATE/`)
- [ ] `CONTRIBUTING.md`

---

### 9. Add Unit Tests

**Status:** Done  
**Files:** `tests/conftest.py`, `tests/test_coordinator.py`

**Implemented:**
- pytest + HA test harness with `pytest-homeassistant-custom-component`
- Coordinator reconfiguration tests (`TestCoordinatorReconfiguration`)
  - `test_update_config_rebuilds_solem_client_with_new_station_count`
  - `test_update_config_regenerates_station_descriptors`
- Entity setup tests (`TestEntitySetup`)
  - `test_entity_descriptors_include_all_stations`
  - `test_battery_entities_are_present`
  - `test_control_buttons_are_present`
- Start/stop button behavior tests (`TestStartStopButtonBehavior`)
  - `test_start_irrigation_calls_solem_client`
  - `test_stop_irrigation_calls_solem_client`
  - `test_api_connection_error_is_surfaces_as_home_assistant_error`
- Irrigation monitor lifecycle tests (`TestIrrigationMonitorLifecycle`)
  - `test_starting_stores_monitor_task_and_marks_active`
  - `test_stop_cancels_monitor_task`
  - `test_failed_start_resets_active_state`
  - `test_async_shutdown_cancels_monitor_task`
  - `test_station_state_optimistically_set_before_command`
- CI job added to `.github/workflows/ci.yml`

**Test execution:**
```bash
cd solem-blip-ha
pip install -e ".[dev]"
pytest -v
```

---

### 10. Add Documentation

**Status:** Partial  
**Remaining:**
- [ ] `docs/DEVELOPMENT.md`, `docs/TROUBLESHOOTING.md`
- [ ] Changelog or release notes per version

---

### 11. Add Config Entry Diagnostics

**Status:** Not started  
**Tasks:**
- [ ] Create `diagnostics.py` with redaction
- [ ] Verify via Settings → Download diagnostics

---

## Removed / Not Planned

### ~~Button device classes (RUN / STOP / ON / OFF)~~

Home Assistant buttons only support `identify`, `restart`, and `update`. Use `translation_key` and icons instead (Task 5).

---

## Implementation Phases

### Phase 1 — Stability / UX ✅
- [x] Task 1: Button error handling + coordinator raises
- [x] Task 2: Diagnostic entity categories
- [x] Task 3: Config validation + station limit alignment
- [x] Task 4: BLE cleanup on unload

### Phase 2 — Maintainability
- [ ] Task 5: Entity translation keys
- [ ] Task 6: Entity description refactor
- [ ] Task 11: Config entry diagnostics

### Phase 3 — Ecosystem
- [ ] Task 7: Device firmware info (when protocol supports it)
- [ ] Task 8: Issue templates and CONTRIBUTING
- [x] Task 9: Unit tests + CI
- [ ] Task 10: Supplementary docs

---

## Cross-repo (`solem-blip-ble`)

Completed in library **v0.1.12** (required by HA manifest):

- [x] `MAX_STATION_NUM = 8` constant; `pack_sprinkle_station()` clamps to 8
- [x] Default parse limit raised from 6 → 8
- [x] Export `MAX_STATION_NUM` / `DEFAULT_MAX_STATION_NUM` from package

Still open in library:

- [ ] `SolemClient.get_firmware_version()` wired to BLE
- [ ] Client-level integration tests (connection lifecycle, retries)

---

## Notes

- Test on actual hardware after releases (especially button error paths and 7–8 station configs).
- Irrigation monitor runs in a stored background task. `_irrigation_active` is set before the BLE start command so coordinator polling does not race. User stop cooperates via event; unload cancels and awaits the task. Only the initial BLE command failure surfaces via `HomeAssistantError` on the button press.
- Requires `solem-blip-ble>=0.1.12` for aligned station limits.

---

## References

- [HA Integration Best Practices](https://developers.home-assistant.io/docs/creating_integration_index)
- [Entity Translation](https://developers.home-assistant.io/docs/integration_translations)
- [Config Flow Guide](https://developers.home-assistant.io/docs/config_entries_config_flow_handler)
- [Data Update Coordinator](https://developers.home-assistant.io/docs/integration_fetching_data)
- [Button Entity](https://developers.home-assistant.io/docs/core/entity/button/)
- [Diagnostics Platform](https://developers.home-assistant.io/docs/core/platform/diagnostics/)
