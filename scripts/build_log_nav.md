# Build log: navigation and layout pass (branch `studio-v3-nav`, from `studio-v3-agent`)

Navigation and layout only. No page content was deleted, and no figure, calculation, report sentence or Lens behaviour was changed, apart from one new Lens view command (`set_details`, below). No AI was called: the fake AI and the shipped sample run were used.

## Done

1. **Five tabs, in order**: Report, Insights, Ideas to try, Dashboard, Data. Ideas to try is its own tab. **Data** has a visible segmented control (not a dropdown, not collapsible sections) with four sub-views, each a full page: Your data, How files connect, What we cleaned, How we analysed. "How the data was set up" (what each column means, its kind, how complete it is) is a section at the top of Your data. The chosen sub-view is remembered per report (browser storage). The Chat tab is gone: Lens "Expand" shows the same conversation full width in place of the page (history and expand view kept). The tabs fit the bar at 1280 px with no horizontal scroll (checked in the browser test).
2. **Export menu** next to the report name: Download PDF (the report PDF), Briefs (a dialog with the saved briefs, each with its Download PDF, or how to ask for one), Power BI files (a dialog with the pack and its download), Print (the print options dialog). The Power BI tab, the "Print / Save as PDF" button and the "Download Power BI pack" button are gone from the page.
3. **One thin bar (48 px)** in the analysis view: logo (home; asks first when answer changes are unsaved), report name, the five tabs, Export, Review answers and a small menu (New analysis, Glossary, History, Full screen). The landing page, Glossary and History keep the original header.
4. **Details.** Needed content is open: the headline, the actions, every chart with its caption, the key table. Moved out of folds: the Dashboard's "More charts (n)" is now an open section; a finding's per-group table is open when it has 8 rows or fewer (longer ones fold as "long tables"). Still folded (supporting detail): "Details" under a report chart, the "Behind the numbers" parts ("How this was calculated" and the step-by-step material), the Insights appendix and data profile, long tables, "What cleaning changed" when it lists more than 8 columns, and Lens's "How this was calculated" and "The query that was run". One **Expand all details** control sits top right of every page and becomes **Collapse details**; it is disabled when a page has nothing folded. The choice is kept with the report's view state (server side; this browser for the shipped sample) and Lens can set it: "show all details", "expand all details", "collapse details" go through the shared view mechanism (new command `set_details`, one undoable step, shown in the slim bar as "all details open", cleared by Reset). Asking for "the details of Enterprise" is still an ordinary question.
5. **Review answers** is a dialog: it closes with the X, Esc and a click outside; focus is trapped while it is open and returns to the button that opened it; when there are unsaved changes (an answer changed or "ask me again" ticked) it asks "Keep editing / Discard and close" first. Dialog titles got unique ids so stacked dialogs keep their own names.
6. **Laptop widths.** At 1100 px and wider the content column is wider (up to 1320 px, 1360 from 1400 px), so 1280 and 1440 use the screen without fullscreen. With the Lens panel docked, the bar drops the logo text and report name. Below 760 px the tabs move to a second scrollable row inside the bar; below 420 px Review answers moves into the ⋯ menu. No horizontal page scroll at 1440, 1280, 1024, 768 and 390 px (browser test).
7. **Old links.** `#/run/<id>/<old name>` opens the new place: `powerbi` and `powerbiexport` open Export > Power BI files, `print` opens Print, `chat` opens the expanded Lens, `model`, `cleaning`, `plan` and `data` (also the full old names) open the matching Data sub-view.

**Item ids and agent coverage.** The backend tab ids did not change (`report, insights, strategy, dashboard, data, model, cleaning, plan, powerbi, chat`), so item ids, focus, chat edits and the agent's tab-coverage test work as before. What Lens is told about the open tab is the sub-view's own name ("What we cleaned"), "Chat" while expanded, and the Power BI items are reachable in the Export dialog. Three catalogue sections (`strategy:list`, `data:tables`, `plan:definitions`) have no page element in the old interface either; they stay in the catalogue.

## Decisions

- Kept the backend tab model and mapped the five tabs onto it, rather than renumbering ids: nothing in Lens, briefs or saved view states had to change.
- Expand all details is a view-state flag, not browser-only, so Lens can set it and it is remembered per report with the rest of the view. It works on every `<details>` of the page, including those inside the report text, so it needs no change to the report's wording.
- The "Ideas to try" tab is always shown (it used to be hidden when a run had no ideas); with none it says so in one sentence.
- "How files connect" is always a sub-view; with one table it says "This analysis used one table, so there are no files to connect."
- Full screen stayed (in the ⋯ menu); New analysis stayed (same menu).
- Print and Download PDF are different: Print opens the options and the browser print dialog; Download PDF is the server-made report PDF.

## Tests

`tests/test_nav.py` (8: the details command, undo and reset, chat setting it, the tab declarations, old-name resolution), `tests/test_ui_nav.py` (10 groups, 66 checks in a real browser: five tabs and the bar, Data sub-views and memory, every section id on its page, Export menu contents, Expand all details and its persistence and chat control, Review answers closing three ways with focus trap/return/unsaved prompt, the logo, old links, widths from 1440 to 390). `tests/test_ui_simplify.py` / `simplify_check.js` were updated to the new tabs (the former setup tabs are Data sub-views, Power BI is in the Export menu, Chat is the expanded Lens); every check they had still passes. The agent tab-coverage test (`test_agent_loop.py`) passes unchanged.

Screenshots (1280 and 1440 px; Report, Insights, Ideas to try, Dashboard, each Data sub-view, Review answers open): `docs/screenshots/nav-before-*.png` and `nav-after-*.png`, made by `scripts/nav_screenshots.js`. The sample has no second file, so there is no "before" picture of How files connect (the old tab was hidden for it).

## Skipped

- No screenshots of the phone layout (it is covered by the width checks).
- The landing tour (StartPage) still shows the old tab names in its own demo window, since it is a separate product tour of the app's content; not changed.
- Lens's own wording about tabs ("the Power BI export tab" in its quoted help texts) was left alone, as Lens behaviour must not change.

**NOT tested with the real AI:** whether a real model proposes `set_details` for requests like "open everything" or "unfold all of it" (the rules read the plain phrases without an AI); how Lens words the effect of the new command; and the PDF download, which needs the server's PDF engine (the link is checked, the file is not).
