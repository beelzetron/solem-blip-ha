# Integration brand icon

Home Assistant only serves PNG brand images from `custom_components/solem_blip/brand/`.
The allowed file names are `icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png` and their
dark variants. Any other file in that folder makes hassfest print a warning. That is why
the editable SVG source and this document live here in `docs/brand/` instead.

The icon is **not** the official SOLEM company logo.

It is original vector artwork for Home Assistant device branding, combining a water drop, leaf shape, and small Bluetooth signal motif.

Official SOLEM trademarks and logotypes (for example assets on [solem-irrigation.com](https://solem-irrigation.com/)) remain the property of SOLEM. This project does not redistribute them. If you are SOLEM or have written permission to use official brand assets, you may replace the files.

The Bluetooth-related motif is a small compatibility cue, not the official Bluetooth logo.

## Files

| File | Location | Size | Purpose |
|------|----------|------|---------|
| `icon.svg` | `docs/brand/` | — | Source artwork (not served by Home Assistant) |
| `icon.png` | `custom_components/solem_blip/brand/` | 256×256 | Standard integration icon |
| `icon@2x.png` | `custom_components/solem_blip/brand/` | 512×512 | HiDPI variant |

Home Assistant 2026.3+ serves the PNGs from `custom_components/solem_blip/brand/` automatically (no manifest key required).

## Regenerating PNGs from SVG

Requires Python with `cairosvg` and `Pillow`:

```bash
python3 -m venv .venv-icon && source .venv-icon/bin/activate
pip install cairosvg pillow

python3 << 'PY'
import cairosvg
from pathlib import Path
from PIL import Image

repo = Path(".")
svg = repo / "docs" / "brand" / "icon.svg"
brand = repo / "custom_components" / "solem_blip" / "brand"

for size, name in [(256, "icon.png"), (512, "icon@2x.png")]:
    tmp = brand / f".tmp_{name}"
    cairosvg.svg2png(url=str(svg), write_to=str(tmp), output_width=size, output_height=size)
    img = Image.open(tmp)
    img.save(brand / name, format="PNG", optimize=True, interlace=True)
    tmp.unlink()
PY
```
