"""Draw the favicon set from the same geometry as frontend/public/logo-mark.svg.

The mark is five rounded rectangles on a rounded plate, so it can be drawn exactly rather than
rasterised from the SVG (no extra dependency, and no blur from a converter).

    python scripts/gen_favicons.py

Writes frontend/public/favicon.ico (16/32/48), favicon-32.png, favicon-180.png (the iOS home
screen) and favicon-512.png (the web manifest). The colours come from the one shared palette.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public"
sys.path.insert(0, str(ROOT / "backend"))
from app import palette  # noqa: E402

PLATE = palette.ACCENT
GLYPH = (255, 255, 255)
SS = 8                      # drawn this many times over and shrunk, for clean edges

# x, y, w, h, radius, opacity — on the 32-unit grid the SVG uses
SHAPES = [
    (7.2, 8.0, 4.4, 7.3, 1.3, 0.42),
    (7.2, 16.7, 4.4, 7.3, 1.3, 0.42),
    (13.8, 8.0, 4.4, 16.0, 1.6, 1.0),
    (20.4, 8.0, 4.4, 7.3, 1.3, 0.42),
    (20.4, 16.7, 4.4, 7.3, 1.3, 0.42),
]
PLATE_RADIUS = 7.5


def draw(size: int, plate: bool = True) -> Image.Image:
    n = size * SS
    k = n / 32
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if plate:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=PLATE_RADIUS * k, fill=PLATE)
    for x, y, w, h, r, alpha in SHAPES:
        layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            [x * k, y * k, (x + w) * k, (y + h) * k], radius=r * k, fill=(*GLYPH, round(255 * alpha)))
        img = Image.alpha_composite(img, layer)
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(32).save(OUT / "favicon-32.png")
    draw(180).save(OUT / "favicon-180.png")
    draw(512).save(OUT / "favicon-512.png")
    draw(48).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    for f in ("favicon.ico", "favicon-32.png", "favicon-180.png", "favicon-512.png"):
        print(f"wrote frontend/public/{f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
