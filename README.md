# Solem BL-IP for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![CI](https://github.com/beelzetron/solem-blip-ha/actions/workflows/ci.yml/badge.svg)](https://github.com/beelzetron/solem-blip-ha/actions/workflows/ci.yml)
[![coverage](./badges/coverage.svg)](https://github.com/beelzetron/solem-blip-ha/actions/workflows/ci.yml)
[![quality scale](https://img.shields.io/badge/quality%20scale-platinum-FFD700.svg)](https://github.com/beelzetron/solem-blip-ha/blob/main/quality_scale.yaml)
[![GitHub release](https://img.shields.io/github/release/beelzetron/solem-blip-ha.svg)](https://github.com/beelzetron/solem-blip-ha/releases/)

Minimal Home Assistant integration for the Solem **BL-IP** Bluetooth irrigation controller.

This is a **separate project** focused on BLE status, battery monitoring, and manual control. Scheduling, rain math, and the Solem Schedule Card are **not** included. If you want the full scheduler integration, use [Henrique Craveiro's original project](https://github.com/hcraveiro/Home-Assistant-Solem-Bluetooth-Watering-Controller).

Requires Home Assistant **2026.3.0** or newer, the first Home Assistant release running on Python 3.14.

## Features

- Controller and per-station status (`on` / `off` / `sprinkling` / `stopped`; translated in the UI)
- Per-station remaining sprinkle time (seconds from BLE while watering)
- Battery percentage, voltage (diagnostic), and low-battery alert
- Manual sprinkle per station, stop, controller on/off
- Configurable manual duration (minutes)
- Read-only on-device program schedule sensors (next start, schedule summary, names)
- Program run detection (`0x44` status) with per-program running binary sensors
- Controller status attributes: active program, program name, watering origin
- Daily controller RTC synchronization after a successful BLE poll
- Repair issue when Bluetooth polling fails repeatedly
- Uses the [`solem-blip-ble`](https://pypi.org/project/solem-blip-ble/) library via Home Assistant Bluetooth

This integration supports the original Solem BL-IP controller running firmware 5.x.
BL-IP V2 controllers running firmware 6.x are not supported.

## Installation

### HACS

Use this link to open this repository in HACS on your Home Assistant instance:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=beelzetron&repository=solem-blip-ha&category=integration)

_or_

1. Install [HACS](https://hacs.xyz/) if needed
2. HACS → **Integrations** → menu (⋮) → **Custom repositories**
3. Add `https://github.com/beelzetron/solem-blip-ha` as category **Integration**
4. Search for **Solem BL-IP** and install
5. Restart Home Assistant
6. Settings → Devices & Services → Add Integration → **Solem BL-IP**

Setup asks only for the **Bluetooth controller** and **number of stations**.

### BLE dependency

Home Assistant installs `solem-blip-ble>=0.1.23` from PyPI automatically. Protocol notes: [solem-blip-ble docs](https://github.com/beelzetron/solem-blip-ble/blob/main/docs/ble_protocol.md).

## Entities (example: 6 stations)

| Entity | Purpose |
|--------|---------|
| Controller status | `on` / `off` / `unknown`; attributes `active_program`, `active_program_name`, `watering_origin` while watering |
| Device firmware | Shown in the Home Assistant device information |
| Battery | 0–100% (from 9V battery level 0–5) |
| Battery voltage | Diagnostic (disabled by default) |
| Battery low | Binary alert |
| Station status | `sprinkling` / `stopped`, using the controller-provided station name |
| Station remaining time | Seconds left while sprinkling (`0` when idle) |
| Irrigation manual duration | Minutes for sprinkle buttons |
| Sprinkle station N | Start manual watering |
| Stop sprinkle | Stop active watering |
| Turn on / off controller | Enable or disable controller |
| Program A/B/C name | On-device program label |
| Program A/B/C next start | Next scheduled run (timestamp + schedule context attributes) |
| Program A/B/C schedule | Enabled start slots, cycle, period length, synchro day, station durations |
| Program A/B/C running | `on` while that program is executing on the controller |

Roughly **34 entities** for a 6-station controller (adds 9 program entities vs earlier builds).

### Monitor a scheduled program run

Compare the **next start** sensor to the **running** binary when verifying on-device schedules:

```yaml
alias: Alert when Program C runs off-schedule
trigger:
  - platform: state
    entity_id: binary_sensor.solem_blip_aabbccddeeff_program_c_running
    to: "on"
condition:
  - condition: template
    value_template: >
      {{ (now() - states.sensor.solem_blip_aabbccddeeff_program_c_next_start.last_changed).total_seconds() > 900 }}
action:
  - action: notify.persistent_notification
    data:
      message: "Program C started more than 15 minutes from its next-start sensor"
```

While watering, check **Controller status** attributes: `watering_origin: program` and `active_program_name` match the running binary.

## Scheduling with Home Assistant

Use native automations (or the HA Scheduler integration) instead of built-in irrigation logic.

### Daily watering at a fixed time

```yaml
alias: Water lawn station 1
trigger:
  - platform: time
    at: "06:00:00"
condition:
  - condition: state
    entity_id: sensor.solem_blip_aabbccddeeff_station_1_status
    state: "stopped"
action:
  - action: button.press
    target:
      entity_id: button.solem_blip_aabbccddeeff_sprinkle_station_1
```

Set **Irrigation manual duration** (`number.*_irrigation_manual_duration`) to control how long each sprinkle runs.

### Skip when rain is forecast

```yaml
alias: Water station 1 unless rain forecast
trigger:
  - platform: time
    at: "06:00:00"
action:
  - action: weather.get_forecasts
    target:
      entity_id: weather.home
    data:
      type: daily
    response_variable: daily
  - condition: template
    value_template: "{{ daily['weather.home'].forecast[0].precipitation | float(0) < 1 }}"
  - action: button.press
    target:
      entity_id: button.solem_blip_aabbccddeeff_sprinkle_station_1
```

Adjust entity IDs and thresholds for your weather integration and layout.

### Skip while raining

```yaml
condition:
  - condition: state
    entity_id: weather.home
    state: "rainy"
    attribute: condition
```

Or use a rain-rate sensor / binary rain sensor from your weather stack.

## Brand icon

Home Assistant **2026.3+** loads the integration icon from `custom_components/solem_blip/brand/icon.png` on your instance. It appears on the **Settings → Devices & Services** integration tile, the device page, and the config flow.

After installing or updating via HACS:

1. Confirm the file exists on your Home Assistant host:
   ```bash
   ls -la /config/custom_components/solem_blip/brand/icon.png
   ```
2. If missing, use HACS → **Solem BL-IP** → **Redownload**.
3. **Restart Home Assistant** (not just reload the integration). HA scans the `brand/` folder only at startup.

**HACS store listing:** The HACS dashboard may still show a blank icon for this integration. That is a [known HACS limitation](https://github.com/hacs/integration/issues/5171) with local-only brand assets; the icon should still work inside Home Assistant itself after restart.

## Options

From the integration **Configure** menu:

- **Scan interval** — BLE poll interval (seconds)
- **Bluetooth timeout** — connection timeout (seconds)
- **Mock Solem API** — debug without hardware

The integration polls BLE status every 60 seconds by default and refreshes the
schedule stored on the controller separately every hour. Failed schedule reads
retry every 15 minutes without delaying status updates. Schedule changes remain
the responsibility of the Solem app or Home Assistant automations.

## Removal

Remove the integration from Settings → Devices & Services → Solem BL-IP. The
controller device is tied to its config entry. Home Assistant only enables independent
device removal after repeated polling failures indicate that the controller is stale.

## Troubleshooting

- Keep mobile apps disconnected while Home Assistant is connecting to the controller.
- Add a Bluetooth proxy closer to the controller if discovery is intermittent.
- If polling fails repeatedly, open **Settings → System → Repairs** and follow the Bluetooth unavailable issue for the controller.
- Use the integration diagnostics download to inspect availability, battery state,
  metadata retry timing, and schedule-read state. The controller MAC address is redacted.
- BL-IP V2 firmware 6.x is intentionally unsupported.

## Credits

See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

## License

MIT — see [LICENSE](LICENSE). Original work Copyright (c) 2025 Henrique Craveiro.
