# AGENTS.md — Solem BL-IP Home Assistant Integration

## Project overview

Home Assistant custom integration for Solem BL-IP Bluetooth irrigation controllers.
Uses `solem-blip-ble` library for BLE communication.
Single package: `custom_components/solem_blip/`.

## Minimum requirements

- **Home Assistant:** 2026.3.0+ (first HA release with Python 3.14)
- **Python:** 3.14+

## Developer commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest -v

# Sanity check (compile all Python)
python -m compileall -q custom_components

# Validate JSON
python -m json.tool custom_components/solem_blip/manifest.json
```

## CI workflow

Runs on push/PR to `main`:
1. **hassfest** — HA core validation
2. **HACS** — HACS validation
3. **Python sanity** — compileall + JSON validation
4. **pytest** — full test suite

## Branching and releases

Use the lightweight Git Flow policy in `docs/branching_and_release.md`.

- Do not commit directly to `main` for normal work.
- Start changes from `feature/<topic>`, `fix/<topic>`, or `hotfix/<topic>`.
- Merge to `main` through a pull request after CI passes.
- Cut GitHub releases only from merged `main`.
- Use immutable `-beta.N` or `-rc.N` pre-releases for changes that need live HA validation before stable release.
- Keep release commits scoped to the HA integration; do not combine BLE library changes.

## Key directories

| Path | Purpose |
|------|---------|
| `custom_components/solem_blip/` | Main integration code |
| `custom_components/solem_blip/brand/` | Icon (loaded at HA startup only) |
| `tests/` | pytest suite |

## Testing conventions

- Async tests use `asyncio_mode = auto`
- `conftest.py` defines fixtures
- Run single test: `pytest -v tests/test_coordinator.py::test_something`

## Brand icon loading quirk

HA only scans `brand/icon.png` at **startup**, not on reload.
After updating the icon:
1. Confirm file exists on host: `/config/custom_components/solem_blip/brand/icon.png`
2. **Restart Home Assistant** (reload is not enough)

## Mock for development

Enable **Mock Solem API** option in integration config to test without hardware.

## Entity layout

- ~25 entities for 6-station controller
- Station entities use names from controller BLE data
- Battery voltage sensor disabled by default (diagnostic)
