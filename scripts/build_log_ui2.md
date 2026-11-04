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

Commit `a00a403`.

---

## Step 2 — an original mark and wordmark

**The concept: a spreadsheet column that has been worked out.** Three columns stand side by side
on a marine plate. The two outer ones are still cells — each split once, the way a column of a
table looks. The middle one has resolved into a single solid figure.

It is the app's own rule drawn as a shape: *the one thing being pointed at is solid, everything
beside it stays quiet*. It reads as a table and as a bar chart at the same time, which is exactly
what the product turns one into. It is also a drawing of the promise in the strapline — the
spreadsheet goes in, one answer comes out — without a magnifier, a swoosh, a speech bubble or the
zigzag line chart that the old mark was (that zigzag is the most-used analytics glyph there is, and
it said nothing about this product).

Five rectangles and nothing else, so it survives 16px: at that size the solid middle bar and the
two split columns are still three distinct shapes. It is not a letterform, so it needs no
hinting; it has no gradient, no shadow and no detail that disappears when it is small.

- `frontend/src/components/Logo.tsx` — the mark in the app. The plate takes `var(--brand)` and the
  shapes `var(--brand-ink)`, so it inverts correctly in dark mode without a second file.
- `frontend/public/logo-mark.svg` — the mark on its own.
- `frontend/public/logo-wordmark.svg` — mark plus "Analyst Studio" set in IBM Plex Sans 600 at
  −0.38 tracking, used in the README.
- `frontend/public/favicon.ico` (16/32/48), `favicon-32.png`, `favicon-180.png` (iOS home screen),
  `favicon-512.png` and `site.webmanifest`, all written by **`scripts/gen_favicons.py`** from the
  same numbers as the SVG and the shared palette — drawn with Pillow rather than rasterised, so
  the edges are exact at every size.

Used in: the landing header and hero (`StartPage.tsx`), the analysis view's thin bar
(`nav/RunBar.tsx`), the landing page header (`App.tsx`), the browser tab, and the README.
The old `.logo-mark` plate in `polish.css`, `theme.css` and `nav.css` is gone — the mark carries
its own plate now.

`npx tsc -b` and `npm run build` clean; `test_ui_nav.py` (the real-browser navigation suite,
which drives the mark as the "back to home" control) → **10 passed**.

---

## Step 3 — click highlight on the browser-drawn charts

Click a bar, slice or month: it takes the accent, every other mark fades to 25%, and a line above
the chart says what was picked with its figure and, for a measure that adds up, its share, e.g.
`Mar: 15.8K (9.1%)`. Click it again and the label goes. Filter-everything is unchanged. The share
is taken from the whole list, not only the bars drawn (`whole` prop), so a "top 7 of 40" chart does
not overstate it; a rate (average, ratio) shows no share. Marks are also reachable by keyboard
(Tab, Enter/Space, `aria-pressed`). `charts.tsx`, `BIDashboard.tsx`, `styles.css`. Commit `64940f9`.

## Step 4 — dashboard readability

- The fit-to-one-screen rule and its machinery (measured height, 24x24 rectangles) are gone from the
  page. The grid is `auto-fill, minmax(340px, 1fr)` and scrolls normally; KPIs and filters stay
  compact at the top. Live charts get a fixed body height (230px, a time series 290px).
  The backend `dashplan` layout and its endpoint are untouched and simply no longer used by the page.
- A finding is a compact card: plain title, trust chip, a one-line takeaway (the finding's own
  headline, ellipsised), and a thumbnail with a 190px minimum height.
- Clicking a card, or "Open full size" on a live chart, opens a dialog (`ChartModal.tsx`, built on the
  existing `Dialog`): the chart at full size, its explanation and the whole "Details, trust and
  next steps" content. Esc closes, focus is trapped, Left/Right (or Previous/Next) move between
  charts. Nothing expands below the card.
- The long hover tooltips are gone (`chart_why` on the card; the confidence reasons on the insight
  card are now written out in the dialog instead).
- **Tests.** Nothing in the Python suite asserted fit-to-screen, so no test was relaxed.
  `test_dashplan.py` tests the backend layout, which is unchanged. Added one browser check,
  `dashboard_card_opens_a_dialog_and_esc_closes_it`, in `tests/ui/widgets_check.js` and
  `test_ui_widgets.py`.
- Checked in a real browser: dialog opens, Right/Left change chart, 30 Tabs keep focus inside, Esc
  closes, click highlight and clear work.
- Run: `test_ui_widgets`, `test_dashplan`, `test_chartsize`, `test_dashboard`, `test_palette` ->
  **55 passed**. `npx tsc -b`, `npm run build` clean (the build's two CSS warnings are old).

## Step 5 — checks

No horizontal scroll at 390px on landing, Report, Insights, Ideas, Dashboard and Data (measured,
`scripts/ui2_shots.js`, no console errors). Reduced motion: `tokens.css` and `polish.css` already
cut every animation and transition under `prefers-reduced-motion`; the dialog and fades use only
those. Screenshots (not committed): `docs/screens/ui2/step4/` (1440, 390) and `step5/` (390).
Not tested: the full suite, and the 1280 / 1920 widths for this part, as asked.

# Part 2 — plain titles, fuller pages, short "How we got this"

No number, trust label or verification changed. Fake AI only. Commits: `198999f` (steps 0, 1, 3), `76b1beb` (step 2), `2d095f3` (steps 4-6), plus the final one below.

## Step 0 — Dashboard cards fill the row
`.bi-flow` is now a wrapping flex row; each card is `flex: 1 1 340px`, so few cards grow and the last row stretches. Measured at 1440 px (1212 px grid): 2 cards 598 px each, 3 cards 393 px, 5 cards 3+2 (598 px on the second row), 8 cards 3+3+2 — right-edge gap 0 px in every row. A lone last card (e.g. 4 cards) takes the full row.

## Step 1 — plain chart titles
- Backend (`tools.py`): the title baked into each chart picture is now the plain name of the finding ("Revenue by Dept", "Revenue over time"), not the sentence. The sentence is unchanged and is still the finding's headline.
- Frontend: `plainTitle()` (display.ts) builds the title from the finding's title and the display names ("profit by sub_category" -> "Profit by sub-category", "A × B" -> "by a and b", "(table)" dropped). Every card (Report "What matters", Insights, Dashboard tile, the full-size dialog) shows that title, with the headline sentence one line beneath it.
- Tests: no Python test depended on the old titles, so none had to be relaxed. `test_text_scan.py` passes unchanged. Added 3 checks to `tests/ui/chartsize_check.mjs` (listed in `test_chartsize.py`'s "wording" group). The heatmap, seasonality and other chart titles are not covered by a text test: not tested beyond the screenshots.
- Existing runs (the sample) keep their old pictures, with the old baked titles. New runs get the short ones.

## Step 2 — Report
One large answer sentence; key figures; "What matters" cards (lead figure with arrow, plain title, result line, small chart, one or two sentences); "What to do" as short actions with the support line folded; a calm data note (warnings the checks raised, or "Nothing to flag"). Two columns at 1100 px and up. "The evidence" is now "What matters" (also in `viewedit.py`, so Lens uses the same name). Nothing is added to the Lens view registry.

## Step 3 — Insights
Summary strip (the answer + the three top points; everything else under "More on the answer"), findings grouped as Top contributors / Trends over time / Comparisons, "Checked, and nothing found" as chips. `themeOf` is unchanged; a new `groupOf` does the grouping. The highest/lowest boxes were removed (the ranking list shows the same).

## Step 4 — How we got this
`HowSteps` (icons + one line each, 3-6 steps) and `TechDetail` ("Show technical detail", closed). Used by the Report (six steps built from the run's own counts), every finding card, the full-size dialog (`InsightCard`). The old content is all still there, inside the fold.

## Step 5 — Data tab
Tiles (rows kept, columns, fixes made, rows left out), a "left out" note, tables with sticky headers inside a scroll box, the cleaning log as a short list with plain verbs (the full table is under "Show technical detail").

## Step 6 — loading, empty, error
One `State` component (icon, title, plain message, what to try). Old class names (`error`, `empty-state`, `placeholder`) stay on it. Used in the run page, Dashboard, Data table, Export briefs, History, and the empty tabs.

## Checks
- Tests run (touched files only): `test_text_scan`, `test_display_names`, `test_new_tools_and_views`, `test_chartsize`, `test_context_insights`, `test_ui_widgets`, `test_ui_nav`, `test_nav`, `test_dashplan`, `test_dashboard`, `test_palette`, `test_ui_simplify`, `test_partb_view`, `test_main_view_polish`, `test_simplify`, `test_old_tab_names` — all passed. The full suite was not run (leave it to a cloud session).
- `npx tsc -b` and `npm run build` clean (the build's two CSS warnings are old).
- Screenshots (not committed) at 1440x900 and 390x844: `docs/screens/ui2/part2/sample/` (the stored sample run) and `docs/screens/ui2/part2/run/` (a fresh rules-only run of `sample_data/mall/sales.csv`, which uses the new chart titles). No horizontal scroll on any tab at either width; no console errors.
- Not tested: a real-AI run, 1280 / 1920 widths, the Ideas to try tab beyond the shared empty state, and print/PDF output with the new Report layout.
- Known: on the Report, "Ideas to try" stays full width at the bottom (the Lens view registry has no slot for the second column).

# Part 2b — titles, chart quality, frames, Export menu, standalone Report, Lens run tools

No number, trust label or verification changed. Fake AI only. Commits are one per step (see `git log`).

## Step 1 — titles and headings
- Bug: `plainTitle` removed any `x` followed by a space, so "mix across" became "mi across". Now a multiplier is removed only when a digit comes first ("47.2x"); a lone "x" between spaces still means "and". Also removed: parentheses, `=`/`<`/`>`/`%` and the figure after them, p-values; "across" reads "by"; at most six words. Tests: `plain_title_keeps_x_inside_words` (mix, tax, box, index, max), `..._still_removes_a_real_multiplier`, `..._has_no_symbols_or_p_values`, `..._is_at_most_six_words` (in `chartsize_check.mjs`, listed in `test_chartsize.py`).
- Headings renamed (frontend and the Lens section names in `viewedit.py`): "How we got this" on a finding is now "Details, trust and next steps"; Ideas card "Evidence and risks"; "Possible explanations to test (not conclusions)" -> "Ideas to test"; "Questions we set out to answer" -> "Questions asked"; "What we checked, and why" -> "Checks run"; "Choices made along the way" -> "Choices made"; "What we left out" -> "Columns left out"; "How your tables connect/Tables/Relationships found/Join steps/Warnings" -> "How files connect/Files/Links found/How files were joined/Things to check"; "Definitions used: derived metrics" -> "Calculated measures"; "Checked, and nothing found" -> "Checked, nothing found"; "Agent activity" -> "Progress"; "What this rests on" -> "Based on". report.md keeps its old headings. No test needed changing for these (none looked for the old text in the page).

## Step 2 — chart picture quality
PNG at 2x, not SVG (every path, the PDF and the Power BI pack use `charts/*.png`). `chartfinish.py`: every backend picture is at most 4.6 in wide and 4.6 in high (3.4 in for stacked share bars), no text under 12 pt, saved at 192 dpi. Share bars: legend below, in-bar labels only for segments of 12% or more, dark text on light segments. Tests: `test_chartfinish.py`.
Before/after of the stacked bar: `docs/screens/ui2/part2b/before/submix_before.png`, `submix_after.png`.

## Step 3 — one frame
`ChartFrame`: same border, padding, radius, thumbnail with a maximum height, "Open full size" popup; inside a fold the thumbnail is 120 px. Used by every picture (`ViewImg`, Insights card, dashboard tile).

## Step 4 — tabs and Export
Power BI is no longer a tab, a dialog or a registry tab. Export menu: Download PDF, Download report.md, Download cleaned data, Download Power BI pack (direct downloads), Briefs, Print. Old `#/run/<id>/powerbi` opens the Report. How we analysed: six one-line steps, an AI usage line (new `ai_usage` totals on the run result) and everything else under "Show technical detail". What we cleaned was already a short list with the detail folded.
Tests changed: `test_simplify.py`, `test_nav.py`, `test_old_tab_names.py`, `test_agent_loop.py` (nine tabs), `lens_eval/requests.jsonl` (e081-e086 no longer mention `report:behind`), and the browser scripts `nav_check.js`, `simplify_check.js`, `widgets_check.js` plus their lists in `test_ui_nav.py`, `test_ui_simplify.py`. README updated.

## Step 5 — the Report as a document
Removed from the Report page: every "How we got this", the ideas list, the cautions note, the AI-partly pill, the mode/footer line. Added by code: a scope line ("Based on 1,500 records, 01 Jan 2018 to 31 Dec 2018, from 2 files"; "orders" only when an order key was verified) and footnotes for a partial last period or one the person said really ended. `report:behind` is gone from the Lens registry. Tests: `test_report_standalone.py`.

## Step 6 — Lens run tools (partial)
The request text was cut off after "(c) a search across the run's". Built: `run_sections` (list), `run_section` (read one of 14 sections), `run_search` (word search across sections), all read-only, chosen by the model from their descriptions, no keyword routing. Tests: `test_run_sections.py` (fake model). Anything that came after "(c)" in the request is not done.

## Checks
Tests run (touched files): test_chartsize, test_chartfinish, test_report_standalone, test_run_sections, test_simplify, test_nav, test_old_tab_names, test_agent_loop, test_agent_custom, test_lens_eval, test_lens, test_token_budget, test_palette, test_text_scan, test_partb_view, test_report_page_stage2, test_report_tiers, test_demo_run, test_display_names: pass. `test_lens_eval.py::test_the_real_ai_mode_refuses_without_a_model` fails when run after `test_lens.py` in one process and passes alone (order-dependent). `npx tsc -b` and `npm run build` clean.
Screenshots (not committed) at 1440x900 and 390x844: `docs/screens/ui2/part2b/after/`. No horizontal scroll.
Not tested: the browser tests (`test_ui_nav`, `test_ui_simplify`, widgets) were edited but not run; print/PDF output; a real-AI run of the new Lens tools; the full suite (leave to a cloud session).
Known: the ranked bar charts and the payment-mode bar chart still have small text where long labels widen the picture, and two x labels overlap on the payment-mode bars; the Report's "What to do" support text is still "Based on" (folded).

# G2 — Report polish

No number, trust label or verification changed. Fake AI only. New module `backend/app/report_lead.py`, called from `get_run` right after `apply_curation`; the selection in `curate.py` is untouched.
1. **Lead figure**: built by code from the saved finding. A comparison leads with the top group's own value ("2,364 — Footwear, average per record"), a rate with its percentage, a trend with the change first to last period, a weekday/month finding with its highest period, a concentration finding with the top share, a link with the strength. "Top compared with the lowest" is gone from the Report cards; the ratio ("The top group is 43.8 times the lowest.") is only inside the card's **Details**. (Insights cards still use the old figure; not asked.)
2. **One plain line** under each title, from the same figures ("Footwear averages 2,364; Accessories averages 776."). No "=", no effect-size words, no p-values; a fallback line is the finding's own sentence with statistics removed, or none if it cannot be made clean. Effect labels appear in Details as "a big difference" etc. The why-it-matters text and merged lines in Details are filtered the same way (sentences with statistics are dropped).
3. **Variety**: the kept Report findings (plus the "more" findings when the kept ones repeat) are ordered one theme at a time (leaders, change over time, link/mix, concentration) and two findings with the same measure and kind of comparison appear only if nothing else exists. A trend or weekday/month finding is drawn with the Dashboard's own `Columns` chart. Note: two cards "MRP by dept" and "Revenue by dept" can both show, as they are different measures.
4. **Same size**: grid rows stretch, the picture area is a fixed 240 px, details are folded. Comparisons are simple bars with the value at the end (6 at most, "Top 6 of n"); error bars and n= stay in the full-size popup (the original picture). Text in the drawn charts is 12 px or more. Findings with no per-group figures (scatter, concentration, mix) still show their picture.
5. **Actions**: `split_action` (bold lead of at most 12 words, one regular line, the rest into "Based on"); no word is lost.
Tests: new `tests/test_report_lead.py` (19 tests: items 1, 2, 3, 5, plus a two-file pipeline check that scans the lead, label, line, why and merged lines for "=", effect words, p-values and "x" ratios). No existing test changed. Also run and passing: test_report_standalone, test_report_tiers, test_report_page_stage2, test_curate, test_text_scan. `npx tsc -b` and `npm run build` clean.
Screenshots (not committed): `docs/screens/ui2/g2/report/` at 1440x900 and 390x844, from a fresh rules-only run of `sample_data/mall/sales.csv`. No horizontal scroll, no console errors.
Not tested: the browser tests (`test_ui_*`), a real-AI run, print/PDF, runs without curation, and the full suite (leave to a cloud session). The stored sample run has no curation, so it still shows the old Report layout.
