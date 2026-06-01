# Contributing

Thanks for helping improve the Solem BL-IP Home Assistant integration.

## Scope

- Target **Solem BL-IP firmware 5.x** only unless a maintainer explicitly expands scope.
- Do not add BL-IP V2 / firmware 6.x support in drive-by changes.
- Keep public docs and code free of private reverse-engineering references.

## Development setup

```bash
cd solem-blip-ha
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
mypy custom_components/solem_blip
```

Use a virtual environment. Do not install test dependencies into the host Python.

## Pull requests

1. Branch from `main`.
2. Keep changes focused and match existing code style.
3. Add or update tests for behavior changes.
4. Ensure CI passes locally when possible:
   - `pytest` (95% coverage gate)
   - `mypy custom_components/solem_blip`
5. Update `README.md` when user-facing behavior changes.

## Quality scale

This integration tracks the [Home Assistant Integration Quality Scale](quality_scale.yaml). New work should preserve or improve documented rule status.

## Related projects

| Repo | Purpose |
|------|---------|
| [solem-blip-ble](https://github.com/beelzetron/solem-blip-ble) | BLE protocol library (PyPI) |
| [solem-blip-ha](https://github.com/beelzetron/solem-blip-ha) | This Home Assistant integration |

Protocol changes belong in the library first; the integration should consume released library versions.

## Reporting issues

Use the GitHub issue templates and attach diagnostics when reporting bugs.
