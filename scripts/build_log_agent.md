# Build log: studio-v3-agent (Lens as a plan-act-verify-answer agent)

Branch `studio-v3-agent`, created from `studio-v3-partb` at `7476849` (clean tree confirmed first). `main`, `backup-cleaned`, `studio-v2`, `studio-v3` and `studio-v3-partb` were not touched. Commits are plain messages with no attribution lines. No real AI call was made: every test and the evaluation replay use scripted fake replies. No figure, calculation or original report sentence was changed.

## Result

- **Full backend suite (once, at the end):** **942 passed, 0 failed, in 229.17 s (3 min 49 s)** (`cd backend && pytest tests -q`). Part B ended at 828; the 114 new tests are `test_agent_loop.py`, `test_agent_custom.py`, `test_agent_briefs.py`, `test_lens_eval.py`.
- **Frontend:** `npm run build` passes (the same two `color-mix` CSS warnings as before). Checked in a headless browser on the sample run: a brief card with its data-quality note, thumbs, an export link, the earlier view controls; no console errors.
- **PDF:** a real PDF of the short report (about 147 KB) and of briefs and a zip of briefs were produced with the bundled Chromium.
- **Evaluation replay (scripted):** `scripts/lens_eval_scripted_result.txt`: 136 requests, 100% plan, 100% ran. This only proves every expected plan is valid and runs; the planner is handed the answer.

## Done

- **The loop** (`backend/app/lens/agent/`): `context.py` (last 6 turns, tab, visible items, focus, understanding, definitions, fact sheet, SQL view, matching stored values; never rows), `planner.py` (system prompt generated from the registry, plan normalisation, validation, one repair), `loop.py` (shortcuts, rule fallback, execute, verify, compose, unmet log), `verify.py`, `registry.py`, `env.py`. In `chat.py` the typed-request path with an AI is now one call to `agent.run_request`; the old single-action prompt, `translate` and `_action_of` were removed.
- **Tools** (one module each, listed in `tools/__init__.py`): data_query, custom_analysis, report_facts, rank, strategies, explain, summarise, glossary, general_knowledge, chart_edit, view_edit, brief, brief_manage, export, clarify, premise_check, recheck_chart. A test adds a dummy tool with only a module-style class and one `register` call, and removes it.
- **custom_analysis** + `sqlguard.py`: sqlglot parse validation (one SELECT/WITH/UNION; one registered view; known columns; DDL, DML, PRAGMA, ATTACH, COPY, INSTALL, LOAD, SET, CALL, EXPORT, table functions, file/network/system functions and a name-prefix deny list rejected; the SQL that runs is regenerated from the tree without comments), DuckDB in memory (external access off, configuration locked, 512 MB, 2 threads, 10 s timeout with interrupt, row caps), sanitised errors, up to 2 repairs, sanity checks (non-empty, NaN or infinite values are a problem and trigger a repair, shares that do not add up are noted), the result as a fact with an id, an optional cross-check that shows "Two methods disagree", label "Custom analysis", SQL collapsed in the box, aggregated results capped at 50 rows and 12 columns for the AI, non-aggregated queries capped at 20 rows for the user only. Unavailable without an AI. `duckdb` and `sqlglot` added to requirements.
- **Briefs:** free-text audience and focus from the plan; evidence queries run through the tools; the writer sees only the fact list; every line is verified (`cited.verify`, causal words, a fact id required for recommendations), failing lines dropped, computed sentences as fallback; fixed layout; the data-quality note and trust label built by code; "The data has no <topic> information" when nothing bears on the focus; several briefs per request saved with the run; list, open, rename, edit ("Edited by you") and delete; a no-AI build from the report's own sentences. A test proves no reader or topic word is in the code, and runs five scripted audiences.
- **Export:** PDF with a headless browser (BROWSER_PATH, bundled Chromium, installed Chrome, Edge; it tries each, because the `google-chrome` wrapper here hung), zip of briefs, report and short report, Copy as text; title, company, date, trust label; no buttons or menus; display names instead of column names (tested). Endpoint `GET /api/runs/{id}/export/{what}`. The Dockerfile installs Chromium.
- **Every tab exposes items** (`viewedit.catalogue` now covers all ten tabs); a test reads the tab list from `frontend/src/tabs.ts` and fails if one is missing or empty.
- **Unmet and feedback:** `lens_unmet.jsonl` (request, tab, time), thumbs up or down on every answer into `lens_feedback.jsonl` with plan and tools, `scripts/lens_feedback_report.py` summarising both.
- **Ambiguity and open questions:** `clarify` choices can be queries or view commands with item titles as labels (validated before offering); a "why" or a wrong premise is still answered by what is behind the number even when the plan is empty.
- **Evaluation set:** `backend/tests/lens_eval/requests.jsonl` (136 requests, 19 categories, 26 custom-analysis), `scripts/lens_eval.py` (scripted planner, or `--real`), plan accuracy by category with failures printed.
- **Docs:** README section on the agent, a loop diagram in `docs/architecture.md`, the request set in `docs/evaluation.md`, Known limits.

## Skipped, partial, or different from the brief

- **No separate "answer" AI call.** Every tool phrases its own result in code, so the loop makes none. The plan may carry a short `reply`, which is verified and dropped when a figure fails. This keeps requests at 1 to 2 calls.
- **Hand view controls** (Hide, Change) are not on the Data, How files connect, What we cleaned, How we analysed, Power BI export and Chat tabs. Their items are in the catalogue, chat view edits on them are saved, and hidden items are honoured only for cleaning steps and plan questions or analyses (with the Hidden items bar); the other tabs ignore hide.
- **Briefs in the interface:** PDF, Copy as text and Delete buttons; rename and edit are by chat only.
- **Custom analysis draws no chart**, only a table.
- **A cross-check runs only when the plan supplies a closed-operation query** (`check_query`); code does not search for one itself.
- **The sample run is shared on a server**, so briefs and feedback made on it are stored in its folder like the existing chat history.
- **Docker image not built.**

## Decisions

- **The request-type routes are now tools, and the plain-rule router is only the no-AI fallback** (`fallback_plan`). With an AI, only chips, `undo`, `reset the view` and hide or show by an exact item title skip the plan call.
- **Old single-action replies are still read** (as a plan of one step), so existing scripted tests keep working; the prompt asks only for plans.
- **Tests changed because a rule route was removed (with an AI present these requests now go to the planner, so they cost one plan call):** `test_lens_fixpack.py::test_graph_is_wrong_rechecks_the_latest_chart` (scripts a recheck_chart plan, 1 call), `test_view_changes_are_not_summarised` (no-AI path unchanged and asserted; with an AI an empty plan is asserted not to be a summary), `test_lens_stage2a.py::test_a_view_change_is_not_refused_as_a_data_question` (one plan call each; styling still gets view-change chips). Nothing else in the existing suite needed changing.
- **A step may depend on an earlier step:** plan validation knows the plan's other steps (an export of a brief written in the same request is valid), and `chart_edit` uses a chart drawn earlier in the same request.
- **One Undo per request:** view edits in a request rewrite the history so undo returns to the state when the request began.
- **Brief writing calls are on top of the 2-call budget** (one per brief, up to 4 briefs), as the brief asks for 2 calls per brief.
- **View commands gained groups:** hide or show every item of a kind, optionally in a tab or section.

## NOT tested with the real AI

- Whether a real model produces valid JSON plans from the generated prompt, picks the right tool, and how often the repair or the rule fallback is needed (run `python scripts/lens_eval.py --real`).
- The quality and dialect-correctness of SQL a real model writes for DuckDB, how often it needs repair, and whether the cross-check is ever supplied.
- The brief writer: how many lines survive verification, how often the computed-sentence fallback is used, and whether briefs for unusual readers are useful.
- Prompt size with a large report and many items (a cap of 80 visible items and the fact sheet limit apply, but no real token count was measured), and pacing/limits against a real provider.
- The "no information on the focus" check on real data with a real model's `columns` list; it was only exercised with a lexical match.
- Chromium PDF export inside the Docker image; PDF layout was not reviewed visually.
- duckdb 2.0 and sqlglot 30 were what ran here; requirements allow older versions that were not tried.


## Fixes from real-AI testing (second round on this branch)

**Result:** frontend build passes; full backend suite **983 passed, 0 failed, in 307.48 s (5 min 7 s)** (includes the 14 new tests in `test_fixes_real_ai.py` and the browser test).

All six are general mechanisms; the example requests are only in `backend/tests/test_fixes_real_ai.py`. Fake AI only.

1. **Selector view changes.** Every item now has `type`, `measure`, `dimension`, `title`, `rank` (within its tab and type, in the report's own order) and `cited` (an action or the summary cites it); `viewselect.py` resolves a selector (type, tab, section, measure, dimension, title words, position, rank, cited, ids) to ids, so a command can touch any number of items. `hide`, `show` and `pin` take `select`; the new `keep_only` takes a `scope` selector and a list of things to keep, each with an optional `best` N (a chart an action or the summary cites comes first), and hides the rest of the scope. "Keep one graph about one measure and the others by another" is one `keep_only` command. A selector that matches nothing says what exists ("There are 19 charts about Revenue (13), Discount pct (4), by Segment, Region"). A change that would hide or show more than 3 items is **previewed** ("I will keep: ... and hide: ...") with an Apply chip that carries the same change as explicit ids; Undo and Reset are never previewed; one Undo reverts the applied change.
2. **Plain language.** Steps in "How this was calculated" and in "I stopped at step 2" use plain labels (`registry.plain`), the refusals no longer mention buttons that do not exist or "view change" jargon, and the PDF error no longer mentions a setting. A test scans every string passed to a message sink in the code (AST) and runs 18 failure cases plus 5 no-AI cases through the chat, and fails on a tool name, "command", "plan", "schema", "JSON", "registry" or any `snake_case` identifier.
3. **Relevant suggestions.** `agent/suggest.py` classifies a refused request as view, data or other from its own words and the report's columns and values; view requests get view examples, data questions get close data questions, anything else gets general report requests. The wording of the offer follows the kind. A missing cost column no longer refuses a request to change the view (the margin check now skips view requests).
4. **Summary scope.** `summarise` takes `scope` (only what the user named); nothing named means the whole report and the answer says "Summary of the whole report."; a tab, a section or a topic named means only that (fact tabs keep the AI wording and every figure check; other tabs and the ideas use the report's own records). The planner prompt says the open tab is never a scope; the no-AI router reads the scope from the words after "summarise". `reports.answer_summary` lead lines changed accordingly.
5. **Superlatives.** `agent/superlatives.py` checks AI-written summary and brief lines against the report's computed top and bottom of each measure and grouping level: a wrong direction is dropped, a missing level is added ("Clothing category brings in the most..."), and a later line that names a different group for the same claim, measure and level is dropped; a reworded summary line is re-verified. The report's own sentences are never altered. One existing assertion changed (`test_summary_lines_are_short_plain_sentences_and_still_checked`) because the level is now named in the line.
6. **Fewer suggestions.** Opening chips: at most 3 with an AI (a view example written from the tab's own sections or items, a data question chosen by what the tab is about, a summary), at most 4 without, chosen by tab and by the last question, with the rest behind a small "More ideas" link; after an answer at most 3; the no-AI "Without an AI model ..." reply shows at most 4 plus "More ideas". The old teaching chips are gone: the opening chips are removed from the panel as soon as the first message is sent. Typed words that read like a change to the view are no longer matched to a chip made for another tab ("Make this tab shorter" means the tab that is open).

Existing tests changed: `test_lens.py` (the opening chips are few, so the tests pick the data chip from the opening and "More ideas" lists), `test_agent_loop.py` (step and failure wording; a data chip rather than the first chip), `test_partb_view.py` (the new refusal sentence), `test_lens_fixpack.py` (level named). Bug fixed on the way: an Undo typed in chat without an AI was applied but not saved when it emptied the history.

**NOT tested with the real AI:** whether a real model writes valid selectors and `keep_only` commands from phrases like the one in item 1; whether it leaves `scope` empty when only a tab is open; how often the superlative check drops a real summary line; whether the view example and data chips read well on every tab; and the bulk preview wording with long real titles (the list is cut at 8 titles).
