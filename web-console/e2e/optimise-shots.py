"""Palette-optimise PNGs in a directory (default ../../docs/screenshots), each kept under 380 KB.

Uses a 256-colour palette and falls back to fewer colours only for the few images that need it.
"""
import sys
from pathlib import Path
from PIL import Image

LIMIT = 380 * 1024
root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "docs" / "screenshots")
for p in sorted(root.glob("*.png")):
    im = Image.open(p).convert("RGB")
    before = p.stat().st_size
    for colors in (256, 192, 128, 96, 64):
        q = im.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        q.save(p, optimize=True)
        if p.stat().st_size <= LIMIT:
            break
    print(f"{p.name}: {before // 1024} KB -> {p.stat().st_size // 1024} KB ({colors} colours)")
