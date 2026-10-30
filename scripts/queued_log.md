# Queued work log

## Prior task (saved answers) - done
Full backend suite: 562 passed. New tests/test_saved_answers.py: 10 passed. Frontend tsc and build clean.
Choices: entries saved before this change have no fingerprint and still match by id+option value; fully covered runs only pause when the new "review_saved" box is ticked (an existing test requires no pause).

## Task A - done (commit "Task A")
Files: backend/app/mainview.py, backend/app/reporter.py, backend/tests/test_content_quality.py (new, 10 tests), backend/tests/test_main_view.py (one assertion edited).
Tests: test_main_view + test_report_consistency + test_growth_scope + new file green; full backend suite 572 passed.
Choices:
- Test edit (allowed kind): test_an_ai_action_without_a_fact_id_is_dropped asserted "No action suggested for question 1"; now a dropped action is replaced by the computed fact line, so it asserts that line instead. Reason: deliberate behaviour change required by Task A item 1.
- "Concrete" action = contains a digit and names the measure of one of the facts offered for that question. Applied to AI actions; strategy actions are not rewritten, only passed through the growth-claim guard.
- Growth/trend labels are applied on an answer card only when it shows both a growth-table figure and a fitted trend; the growth label is parsed from the fact's short text (keeps the figure, drops the "from a to b" range).
- Units: % for measures whose name has pct/percent/rate/share/ratio, currency symbol for money columns; only bare numbers after averages/mean/median/is/at/was/to are changed. Counts get no unit added (limitation).
- The reason in "No action suggested" never quotes the dropped sentence.

## Task B - done (commit "Task B")
Files: backend/app/aicache.py, llm.py, concerns.py, understanding.py, reporter.py, tests/conftest.py, tests/test_trust_cache.py, tests/test_ai_call_cache.py (new, 6 tests), scripts/scorecard.py.
Tests: full backend suite 578 passed (first run: 6 failed, fixed once, see below).
Choices:
- Caching sits in llm.ask_json_ex / ask_text. A JSON reply is stored only after it parses; failed, rate-limited and unparseable calls are never stored. Key = sha256(system+JSON suffix, user, model name, temperature).
- Stage name in the "Reused saved AI result: ..." report line is module.function of the caller (e.g. reporter.llm_summary), plus "concerns" and "understanding" for their own caches. The line is in "Data and cleaning" and only lists stages reused before the report is built.
- The first full run had 6 failures (rate_limit x2, propose x2, plan_proposal, kpi_ratio_determinism): fake models there give different answers to the same prompt, which the disk cache then hid. Fix: conftest sets AI_CACHE=off for every test; test_ai_call_cache.py and test_trust_cache.py switch it on with an autouse fixture. No assertion was changed.
- scorecard.py prints "AI requests sent in this run" using llm.SENT (requests really sent in the process).

## Kaveri scorecard (python scripts/scorecard.py --dataset kaveri --runs 1, twice, cache on)
Outputs: scripts/kaveri_q1.txt, scripts/kaveri_q2.txt. No daily rate limit was hit.
- Run 1: 12/12 passed (all 12 checks PASS), AI requests really sent: 24. Two stages already reused saved results (relations.llm_review, critic.review: same prompts were on disk from earlier runs).
- Run 2: 12/12 passed (same 12 checks PASS), AI requests sent: 0. Report line: "Reused saved AI result: relations.llm_review, derived.propose, propose.propose, critic.review, insights.enrich_with_llm, strategy.refine_with_llm, summary.llm_pick, reporter.llm_summary."
- Per-check counts: 12/12 checks passed in each run (check-by-check pass count 1/1 each).

## Summary
- Prior task (saved answers): done. Task A: done. Task B: done. Everything on branch queued-work (main untouched). Commits: WIP checkpoint, Task A, Task B.
- Test counts: saved answers 10 new, full suite 562; Task A 10 new, full suite 572; Task B 6 new, full suite 578 (after one fix).
- Not committed on purpose: scripts/sc_*.txt, output .txt files, .env, backend/data.
