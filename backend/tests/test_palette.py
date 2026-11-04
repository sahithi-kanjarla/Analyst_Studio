"""The data palette has one source: the chart-tokens block of frontend/src/tokens.css.

backend/app/palette.py and frontend/src/palette.ts are generated from it by scripts/gen_palette.py.
These tests fail if the three ever drift apart, and check the contrast and the rules the palette
promises: AA for every text colour, one accent, and a categorical ramp that is not a rainbow of
look-alikes.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKENS = ROOT / "frontend" / "src" / "tokens.css"

sys.path.insert(0, str(ROOT / "backend"))
from app import palette  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import gen_palette  # noqa: E402


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[1 + i * 2:3 + i * 2], 16) / 255 for i in range(3))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)))
    return (lb + 0.05) / (la + 0.05)


def css_var(name: str) -> str:
    text = TOKENS.read_text(encoding="utf-8")
    m = re.search(rf"--{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{6}})\s*;", text)
    assert m, f"tokens.css has no --{name}"
    return m.group(1).lower()


def test_the_generated_palette_files_are_up_to_date():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_palette.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_python_and_typescript_hold_the_same_colours():
    ts = (ROOT / "frontend" / "src" / "palette.ts").read_text(encoding="utf-8")
    for css_name, py_name in gen_palette.NAMES.items():
        value = css_var(css_name)
        assert getattr(palette, py_name) == value, py_name
        assert f'export const {py_name} = "{value}";' in ts, py_name
    assert palette.CATEGORICAL == [css_var(f"cat-{i}") for i in range(1, 7)]
    assert palette.CATEGORICAL[0] == palette.ACCENT          # the highlight leads the ramp


@pytest.mark.parametrize("fg,bg", [
    ("INK", "PANEL"), ("MUTED", "PANEL"), ("ACCENT", "PANEL"),
    ("GOOD", "PANEL"), ("CAUTION", "PANEL"), ("BAD", "PANEL"),
])
def test_every_text_colour_meets_wcag_aa_on_the_panel(fg, bg):
    assert contrast(getattr(palette, fg), getattr(palette, bg)) >= 4.5


def test_the_page_colours_meet_aa_too():
    for name in ("ink", "muted", "brand", "ok", "warn", "err"):
        assert contrast(css_var(name), css_var("bg")) >= 4.5, name


def test_a_highlighted_mark_is_clearly_darker_than_a_neutral_one():
    """The data rule is read by lightness as well as hue, so it still works in greyscale."""
    assert luminance(palette.NEUTRAL) / luminance(palette.ACCENT) > 3
    assert luminance(palette.NEUTRAL_SOFT) > luminance(palette.NEUTRAL)


def test_no_two_categorical_colours_look_alike():
    """Neighbours in the ramp differ enough that colour-blind readers can still tell them apart:
    either a clear lightness step or a clear step in the blue/red balance."""
    def rgb(c):
        return [int(c[1 + i * 2:3 + i * 2], 16) for i in range(3)]
    for i, a in enumerate(palette.CATEGORICAL):
        for b in palette.CATEGORICAL[i + 1:]:
            ra, rb = rgb(a), rgb(b)
            lightness = contrast(a, b)
            # how differently the two sit on the blue-yellow axis, which every kind of
            # colour-blindness keeps
            blue_yellow = abs((ra[2] - (ra[0] + ra[1]) / 2) - (rb[2] - (rb[0] + rb[1]) / 2))
            assert lightness >= 1.35 or blue_yellow >= 45, (a, b, round(lightness, 2), blue_yellow)
