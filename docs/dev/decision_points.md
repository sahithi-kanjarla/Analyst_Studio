# Decision points audit

Which decisions are made by a rule or word list, which by the LLM, and which by both.
Today the LLM is already involved in the plan, question wording, metric polarity, ratio proposals,
join vetoes and prose. Every LLM path is validated and falls back to rules.

Legend: **rule** = code only; **LLM** = LLM only; **both** = LLM proposes and rules validate or finish.

| # | Decision | Where | Decided by |
|---|---|---|---|
| 1 | Column roles (id / binary / categorical / numeric / text / date) | `profiler.py:12,127-169` | rule |
| 2 | Column scope (plausible dimensions 2-30 unique, measure needs >=10 unique, `relevant_columns`) | `planner.py:27,56-61,193`; `tools.py:1056` | both (LLM lists relevant/skipped columns; rules set the scope) |
| 3 | Target choice (goal/KPI token overlap, `PRIOR_TARGET` regex, industry hint) | `planner.py:100-114` | both |
| 4 | Which analyses enter the plan, their order and the cap (14 / 18), top-ups, extras | `planner.py:132-191`, `finalize_plan` 234-287 | both (LLM proposes; rules cap, order and top up; prompt says 10 but code accepts 18) |
| 5 | Goal intent "asks about change" (`CHANGE_RX`) | `goal.py:9-54` | rule |
| 6 | KPI-to-column mapping (token overlap) | `context.py:162-186`; `planner.py:100`; `insights.py:349,442` | rule |
| 7 | **(b) Industry lens** (`INDUSTRIES` keyword dict, substring hit counts, target hints, levers, data-gap regexes) | `context.py:17-104`; used in `planner.py:95,103`, `insights.py:175,465`, `strategy.py:55` | rule |
| 8 | **(a) Measure is additive (sum) or a rate (mean)** (`SUM_WORDS` / `AVG_WORDS`; money-column regex copies) | `tools.py:33-39,69,145`; `planner.py:146,189`; `dashboard.py:20,42`; `insights.py:419-420`; `powerbi.py:37,50`; `relations.py:236` | rule |
| 9 | Chart type per finding and the "why this chart" text | `insights.py:99-116,360` | rule (fixed per tool) |
| 10 | Dashboard measures, dimensions, donut dimension, hero charts, gallery picks | `dashboard.py:56-89`; `BIDashboard.tsx:156-221`; `Dashboard.tsx:47`; `FindingGallery.tsx:6` | rule |
| 11 | Metric direction / polarity | `polarity.py` | both (LLM, validated against a heuristic; id-like always neutral) |
| 12 | Hierarchy detection | none exists (only the FK graph in `relations.py`) | net-new |
| 13 | Table links and base table | `relations.py:104-190,342` | both (LLM can only veto links; its base-table suggestion is ignored) |
| 14 | Derived ratios | `derived.py` | both (LLM proposes; numeric checks validate) |
| 15 | Which findings go in the summary, headlines and recommendations | `reporter.py:24-61`; `insights.py:120-170,346-361` | both (rules rank by score; LLM only words it, with numbers checked) |
| 16 | Strategy selection, priority and cap (8) | `strategy.py:53-270` | rule selects; LLM rewords |
| 17 | Critic follow-ups and cautions | `critic.py:47-98` | both |
| 18 | Which clarifying questions to ask | `signals.py` (detection), `clarify.py:290-312` | detection: rule; wording: LLM; selection and cap: rule |
| 19 | **(c) Binary positive-label guess** (`pos_words`, else last label alphabetically) | `tools.py:141-146` | rule |
| 20 | Imputation, outlier flags, duplicate removal | `cleaner.py` | rule (stays: deterministic data handling) |

## Intentionally stays rule-based
Signal detection, the statistics themselves, significance and confidence thresholds, number validation, and data cleaning.

## Notes
- Mixed-mode runs exist: the plan can be rules-only while polarity, insights, strategies, summary and critic each succeed or fail with the LLM separately.
- The reporter returns its mode (`llm` / `rules`) but it is not stored in state or `result.json`; today only the timeline event records it.
- The Plan tab's "Deliberately skipped" list is empty on the rules path.
