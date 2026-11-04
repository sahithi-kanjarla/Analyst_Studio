# Build log — `studio-v3-ui2`

Branch from `studio-v3`. A presentation pass only: **no number, trust label or verification was
changed anywhere**. Fake AI / rules-only throughout; no real model call was made.

Screenshots for each step are written to `docs/screens/ui2/<step>/` at 1280×720, 1440×900,
1920×1080 and 390×844 by `scripts/ui2_shots.js`, and are **not committed** (the folder is in the
repository's local `info/exclude`).

---

## The design plan, before any code

**Subject.** Analyst Studio is a verified-arithmetic analyst for a small online seller: someone
with a sales spreadsheet, no statistics training, and a decision to make this week. The page's one
job is to make it obvious **which figure is the point** and to make every figure feel checked.

**What the old look was.** Warm cream page (`#f7f5f0`) with a terracotta accent (`#a4461f`) and
warm greys. That combination — cream near `#F4F1EA` plus a terracotta near `#D97757` — is the
single most common signature of generated UI, so it had to go whatever else changed.

**Colour.** Paper-white surfaces, cool ink greys, one marine accent.

| token | value | what it is |
|---|---|---|
| `--bg` | `#f5f6f7` | the page: cool near-white, not cream |
| `--card` | `#ffffff` | a measured surface |
| `--ink` | `#16191d` | graphite text |
| `--muted` | `#5a636e` | secondary text, 5.7:1 on the page |
| `--line` | `#dde1e6` | hairlines |
| `--brand` | `#15567f` | marine, the one accent, 7.9:1 on white |

Status stays separate from the accent: `--ok #0e6b4f`, `--warn #8a5a00`, `--err #b0271b`, each AA
on the page and on its own tint.

**Why marine and not a teal, a green or another warm tone.** The accent sits on a bar directly
beside a good / watch / problem chip. Blue is the only family that never reads as a verdict on a
sales figure, so a marine bar says "this is the one I mean" and not "this is good". It is also
deep and low-chroma, so it stays quiet next to a white card instead of behaving like a SaaS indigo.

**The data palette.** One source: the `chart-tokens` block of `frontend/src/tokens.css`.
`scripts/gen_palette.py` reads that block and writes `backend/app/palette.py` (what matplotlib
draws the report, Lens and Power BI charts with) and `frontend/src/palette.ts` (what the
browser-drawn charts use). `backend/tests/test_palette.py` fails if the three ever drift apart. The
rule everywhere: **the highlighted or selected mark carries the accent, every other mark a muted
neutral.** The categorical ramp stops at **six** — marine, ochre, green, rose, sky, graphite —
because the test proves no seventh fill can be added that a colour-blind reader can still tell from
the rest. The first attempt had eight; the test rejected teal-green against violet, and then marine
against teal-green, which is exactly the kind of pair that looks fine to me and is invisible to a
deuteranope.

**Type.** IBM Plex Sans, one family, two roles: 600 with −0.02em tracking for display, 400/500 for
body; IBM Plex Mono for code and file names only. Chosen for its engineered lining figures and its
instrument-panel voice rather than a startup one, and because `tabular-nums` is applied to every
figure in the app so a column of numbers lines up and a figure does not jump as it updates. Scale:
13 / 15 / 17 / 21 / 28 / 40.

**Layout and restraint.** Content on a 1120 column, reading text under 68 characters, everything
left-aligned. Three radii rather than one (4px chips, 10px cards, 14px the single dialog), because
one radius on everything regardless of hierarchy is part of the generated-card look. **One**
shadow, used only by things that float — the dialog and the menus. A card answers a hover with its
hairline. No gradient, no blob, no emoji, no all-caps tracked eyebrow, no `→` glued to link text.

---

## Step 1 — design tokens, one accent, one shared data palette

**`frontend/src/tokens.css`** is the one place colour, type, spacing, radius and shadow are
decided; the new scheme above replaced the brown and grey one, and dark mode was rebuilt to match.

**One shared place for chart colour.** `scripts/gen_palette.py` → `backend/app/palette.py` +
`frontend/src/palette.ts`, with `palette.rc()` carrying the matplotlib settings every chart shares
(including `axes.axisbelow`, which the first screenshot showed was missing: the grid was drawing
white stripes across the bars). Pointed at it:

- `backend/app/tools.py` — `PALETTE`, `NEUTRAL`, `OTHER_COLOR`, the ranking and Pareto bar colours,
  the heatmap ramp (a white→marine ramp replaces matplotlib's `Blues`) and its cell text.
- `backend/app/reportcharts.py` — ink, muted, line, panel and the highlighted bar.
- `backend/app/lens/render.py` — accent, neutral, series, grid, panel, the marked point.
- `backend/app/powerbi.py` — the starter pack's theme.
- `backend/app/lens/exporter.py` — the exported HTML/PDF stylesheet.
- `frontend/src/components/charts.tsx` — re-exports the shared values and adds `shade(i, n)`, a
  one-hue lightness ramp for the share ring, so a donut is never a rainbow.

**Stale colours pointed at the tokens** in `styles.css`, `polish.css` and `landing.css` (the ER
diagram, the status dots, the share bar, the KPI note, the gallery tiles, the code block, the
dashboard hover). `ERDiagram.tsx` and `Timeline.tsx` now read the shared palette instead of
holding hex values.

**Generated-UI tells removed:** every all-caps tracked label (`.kicker`, `.rp-kicker`,
`.brand small`, `.recent h3`, `.side h3`, `.hist-group h4`, `.print-panel legend`, the insight and
strategy `h5`, the table headers) is now sentence case; the emoji in the ER diagram's role marks
and the thumbs on a Lens answer are gone (the latter now say "Helpful" / "Not helpful", which is
better copy anyway); card hovers no longer grow a shadow.

**The shipped sample run had to be redrawn.** Its chart PNGs are committed files, so the sample
report kept its terracotta bars after the palette changed. `scripts/redraw_charts.py <run dir>`
replays each finding's own tool with the parameters the run recorded and redraws the report page's
charts from the same specs — **figures, wording and trust labels in `result.json` are not touched,
only the PNG bytes.** It redrew 14 finding charts and 1 report-page chart with no failures.

**Tests.** New `backend/tests/test_palette.py` (11 tests): the generated files are up to date, the
Python and TypeScript values agree with `tokens.css`, every text colour meets WCAG AA on the panel
and on the page, a highlighted mark is at least 3× darker than a neutral one so the rule survives
greyscale, and no two categorical colours look alike.
`test_report_page_polish.py::test_charts_use_the_apps_font_and_readable_colours` was updated: it
used to assert the old `styles.css` values and `"Inter"`; it now checks the chart colours against
the `chart-tokens` block and the font against `palette.FONTS[0]`.

Run here: `test_palette.py`, `test_report_page_polish.py`, `test_chartsize.py`, `test_demo_run.py`,
`test_new_tools_and_views.py`, `test_render_lock.py` → **76 passed**; `test_lens.py`,
`test_lens_fixpack.py` → **74 passed**. `npx tsc -b` and `npm run build` both clean. The full suite
is too heavy for this laptop and has **not** been run here.

Screenshots reviewed at all four widths: no horizontal scroll at 390px, no console errors.
