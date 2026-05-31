# Solem BL-IP for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![CI](https://github.com/beelzetron/solem-blip-ha/actions/workflows/ci.yml/badge.svg)](https://github.com/beelzetron/solem-blip-ha/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/release/beelzetron/solem-blip-ha.svg)](https://github.com/beelzetron/solem-blip-ha/releases/)

Minimal Home Assistant integration for the Solem **BL-IP** Bluetooth irrigation controller.

This is a **separate project** focused on BLE status, battery monitoring, and manual control. Scheduling, rain math, and the Solem Schedule Card are **not** included. If you want the full scheduler integration, use [Henrique Craveiro's original project](https://github.com/hcraveiro/Home-Assistant-Solem-Bluetooth-Watering-Controller).

Requires Home Assistant **2026.3.0** or newer, the first Home Assistant release running on Python 3.14.

## Features

- Controller and per-station status (Stopped / Sprinkling)
- Per-station remaining sprinkle time (seconds from BLE while watering)
- Battery percentage, voltage (diagnostic), and low-battery alert
- Manual sprinkle per station, stop, controller on/off
- Configurable manual duration (minutes)
- Uses the [`solem-blip-ble`](https://pypi.org/project/solem-blip-ble/) library via Home Assistant Bluetooth

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

Home Assistant installs `solem-blip-ble>=0.1.14` from PyPI automatically. Protocol notes: [solem-blip-ble docs](https://github.com/beelzetron/solem-blip-ble/blob/main/docs/ble_protocol.md).

## Entities (example: 6 stations)

| Entity | Purpose |
|--------|---------|
| Controller status | On / Off / Unknown |
| Device firmware | Shown in the Home Assistant device information |
| Battery | 0–100% (from 9V battery level 0–5) |
| Battery voltage | Diagnostic (disabled by default) |
| Battery low | Binary alert |
| Station status | Stopped / Sprinkling, using the controller-provided station name |
| Station remaining time | Seconds left while sprinkling (`0` when idle) |
| Irrigation manual duration | Minutes for sprinkle buttons |
| Sprinkle station N | Start manual watering |
| Stop sprinkle | Stop active watering |
| Turn on / off controller | Enable or disable controller |

Roughly **25 entities** for a 6-station controller.

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
    state: "Stopped"
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
condition:
  - condition: template
    value_template: "{{ state_attr('weather.home', 'forecast')[0].precipitation | float(0) < 1 }}"
action:
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

## Credits

See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

## License

MIT — see [LICENSE](LICENSE). Original work Copyright (c) 2025 Henrique Craveiro.
