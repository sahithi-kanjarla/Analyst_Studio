# Flow audit: who decides what in Analyst Studio

Audit only. Nothing was changed and no tests were run. Based on reading `backend/app/*.py` at the working-tree state of `main` (uncommitted edits included).
Token sizes in section 6 are **estimates** from prompt text length (about 4 characters per token), not measurements.

Intended flow: (1) the AI reads profile + goal + company context and decides meanings, calculations, oddities, questions and plots. (2) Code computes facts, checks that the AI's choices are possible and executes them. (3) AI wording is checked against computed numbers. (4) Rules-only is a thin fallback.

Graph order (`graph.py:538-556`): profile -> relate -> brief -> clarify -> (pause) -> plan -> clean -> analyze <-> critic -> insights -> strategy -> report -> powerbi.

Short verdict: the AI is **consulted** at almost every stage, but only the brief, the plan, polarity and the wording stages let it *decide* anything. Every AI decision is filtered by a code rule, and in about a dozen places code decides a meaning on its own or overrules the AI. What is "AI-made" is mostly column roles/kinds/KPIs, the analysis list (before code tops it up), question wording, and prose.

---

## 1. Decision map

"Who": **AI** = model decides, code only checks. **code** = rules only. **both** = AI proposes and code decides what survives, or code decides and AI words it.

| Stage | Decision | Who decides today | File / function |
|---|---|---|---|
| Profile | Column role (id / binary / categorical / numeric / text / date / empty) | code (names + values) | `profiler.py` `infer_column`, `_numeric_role` |
| Profile | Text -> number / date conversion, NA tokens | code | `profiler.py` `try_numeric`, `try_datetime`, `normalize_strings` |
| Relate | Which tables link, base table, aggregation | code; AI may only veto links | `relations.py` `detect_relationships`, `choose_base`, `llm_review` |
| Relate | Placeholder numbers (999, max repeated, blank twin) | code detects, user is asked (default: missing) | `placeholders.py` `detect`, `graph._placeholders_before_join` |
| Brief | Column role id / dimension | both (AI proposes; validator may flip measure->id; `apply_roles` applies only id and dimension) | `brief.py` `_make_validator`, `apply_roles` |
| Brief | Column kind additive / rate / other | both (AI answer wins per column; name regex otherwise) | `brief.py`; `measures.py` `measure_kind` |
| Brief | KPI name -> column | both (AI; code checks numeric, not id) | `brief.py` validator `kpis` |
| Brief | Ratio KPI numerator / denominator | both: AI, then 2nd AI call, then rules | `brief.resolve`, `derived.rules_ratio` |
| Brief | Hierarchy parent > child | both (AI proposes; code verifies on data) | `brief._check_hierarchy` |
| Brief | Derived measures (mul / div / sub / add), line-total column | both (AI proposes; code accepts or rejects) | `derived_check.judge` |
| Brief | Goal intent, industry | AI; fallback regex / keywords | `brief.py`; `goal.asks_about_change`; `context.detect_industry` |
| Clarify | Which ambiguities exist (sentinels, negatives, near-sum, duplicate columns, rare labels, multi-date, high missing, product basis, flag, blank, date order) | code only | `signals.py` `compute_signals`, `retail_roles.derive_product_measure` |
| Clarify | Question wording and options | both (AI writes for non-code-owned signals; code templates otherwise; product / flag / blank / date-order always code) | `clarify.py` `llm_questions`, `_template`, `product_question` |
| Clarify | Which questions survive, which are asked (max 3), defaults | code | `clarify.validate_question`, `cap_questions`, `resolve` |
| Plan | Derived ratios (extra) | both: AI proposes in a 2nd prompt, code checks stats | `derived.propose`, `check_ratio`; KPI ratios `derived.kpi_ratios` (code) |
| Plan | Target, date column | both (AI -> validator -> user/brief override -> rules) | `planner.propose_plan`, `finalize_plan`, `heuristic_plan` |
| Plan | Analyses chosen (max 14 / 18) | AI proposes; validator all-or-nothing | `planner.plan_validator` |
| Plan | Analyses added | code (uncapped) | `ensure_required`, `measure_extras`, `_include_extras`, change-trend injection |
| Plan | Relevant columns / skipped list | both (AI list + every used column + every 2-30 distinct category) | `finalize_plan`, `plausible_dimensions` |
| Plan | Id-like exclusion | code | `planner.id_like_columns`, `profiler.integer_code_like` |
| Clean | Types, NA tokens, label merge, duplicates, median impute (<=30%), "Unknown", outlier flag, directives | code | `cleaner.py` `coerce_types`, `scoped_clean`, `apply_directives` |
| Analyst | Every number: tests, sums vs means, trends, charts, positive label of a binary column | code | `tools.py` (16 tools), `to_binary`, `additive` |
| Critic | Bonferroni, small groups, skew follow-ups, trend length | code | `critic.review` |
| Critic | Extra cautions, <=2 follow-ups | AI, validated; notes dropped if contradicted | `critic.review`, `wording.contradicts` |
| Insights | Metric direction (higher / lower / neutral) | both (AI, validated; name heuristic fills gaps; id words force neutral) | `polarity.py` |
| Insights | Headline, why-it-matters, hypotheses, confidence, score, chart type | code templates (per tool) | `insights._narrative`, `confidence`, `CHART_CHOICE` |
| Insights | Rewrite of why / hypotheses / next steps | AI, numbers checked | `insights.enrich_with_llm` |
| Insights | Key metrics, data gaps | code (regex on column names + brief roles) | `insights.key_metrics`, `data_gaps` |
| Strategy | Which strategies exist, impact, effort, priority | code templates from significant findings | `strategy.candidates`, `prioritise` |
| Strategy | Wording | AI, numbers checked, up/down-word veto | `strategy.refine_with_llm` |
| Report | Group rankings, growth, "rate outside main value" | code | `summary.ranking_facts` |
| Report | Which fact answers which goal question | both (AI picks ids from a closed list; code word-match fallback and override) | `summary.pick`, `llm_pick`, `goal_pick`, `rules_pick` |
| Report | Goal answer sentence, headlines, evidence tiers, strategy merge | code | `goal.answer`, `summary.headlines`, `tiering.build_view` |
| Report | Executive summary and recommendations | AI, sentence-level number check; else rules | `reporter.llm_summary`, `rule_summary` |
| Output | Dashboard measures / dimensions / charts, Power BI measures | code | `dashboard.build_dashboard`, `powerbi.py` |

---

## 2. Where code decides a meaning, or overrides / ignores the AI

One line each: what code decides | what the AI said | who wins.

1. `profiler.infer_column` / `_numeric_role`: role from values (<=8 distinct integers = category, 2 values = binary, etc.) | brief role | AI wins only for `id` and `dimension` (`brief.apply_roles`); AI `measure`, `date`, `text` are ignored in the profile. Code wins.
2. `brief._make_validator`: AI `measure` on a column whose name matches `ID_NAME` becomes `id` | AI said measure | code wins (see bug 1: `ID_NAME` is too broad).
3. `retail_roles.demote_quantities` / `is_count_like` / `is_rate_like`: any whole number >=1 with <=30 distinct values and median <=20 is a "quantity", never a group; positive many-valued numbers are "rates/prices" | AI said nothing (rules-only) or `dimension` | AI `dimension`/`id` wins; AI silence -> code. In rules-only mode a class 1/2/3 or month-number column cannot be grouped.
4. `retail_roles.derive_product_measure` / `_ai_product`: decides count x rate = amount, per-unit vs line total, from values | AI proposed `mul` | code can silence the AI (`_already_holds`, basis `total` -> no signal, no column); if the AI answered, the value rule is skipped entirely.
5. `signals.compute_signals`: code decides what looks odd (sentinels -1/99/999, zero/edge spikes, negatives, near-sum, duplicates, rare labels, high missing, flag columns, date order) | the AI never sees raw values | code wins; a question with no backing signal is dropped (`clarify.validate_question`).
6. `clarify.validate_question` / `cap_questions`: code drops AI questions whose answers change nothing, forces the default to be "harmless", caps at 3 by a fixed `IMPACT` order | AI's own default and priority | code wins.
7. `clarify.llm_questions`: an AI answer of `questions: []` is accepted as "ask nothing" | strong signals existed | AI wins, templates are not used (the design says strong signals are the ones that silently change numbers).
8. `placeholders.question`: default answer = "treat as missing" (changes data) for a number repeated at the column's max/min | not AI at all | code wins; if the question is capped it runs by default (`cap_questions` keeps non-harmless defaults as `auto: capped`).
9. `cleaner.normalize_strings` (via `profiler`, `coerce_types`): "none", "missing", "undefined", "-", "?", "n/a", "null", "nan" in text columns become NaN | AI/user never asked | code wins silently (only logged).
10. `cleaner.scoped_clean`: median imputation of every relevant numeric column with <=30% missing except the target; categorical blanks -> "Unknown"; duplicates dropped when any id/numeric/date/text column exists | brief KPI map, kinds | code wins; only `plan.target` is protected, KPI columns and ratio parts are not.
11. `planner.finalize_plan` target override: when `ov["target"]` is set (user answer, the brief's `already_total`, or a product measure) and differs from the AI target, **all** AI analyses are discarded and rebuilt by rules | AI planned around its own target | override wins, whole AI plan lost. The note says "The main number you chose", even when the brief (AI) chose it.
12. `planner.ensure_required`, `measure_extras`, `_include_extras`, change-trend injection: code appends analyses (hierarchy parents, drill-downs, KPI columns, flag-rate by group, group share table, correlation, seasonality) | AI plan | AI order kept, code adds on top, uncapped; prompt tells the AI not to spend its budget on these.
13. `planner.candidate_measures` / `kpi_measures`: a "measure" is any numeric column with >=10 distinct values that is not id-like | AI `relevant_columns` | code decides what is a candidate measure and analyses all of them (`MAX_EXTRA_MEASURES` = 3 charted, 12 in the summary table).
14. `planner.plausible_dimensions`: every 2-30 distinct category is added to `relevant_columns` | AI `skipped` list | code wins; the AI skip list can contradict the final relevant list.
15. `planner.heuristic_plan` target scoring (`PRIOR_TARGET` regex, KPI token overlap, industry regex) | AI target | AI wins when valid; regex is the fallback. Candidates are not filtered by `id_like_columns`.
16. `tools.validate_spec` / `plan_validator`: AI plan accepted only if tools and column roles/cardinality fit (group needs 2-30 distinct values) | AI | code wins; one bad step rejects the whole answer (repair, then rules plan).
17. `measures.measure_kind` (name regex SUM_WORDS / AVG_WORDS) and `is_money` | brief `kinds` | AI wins per column when answered; every column the AI skipped, and every currency-symbol decision (`is_money`), is name-regex only.
18. `tools.to_binary`: positive label = first of `yes, y, true, ..., churn, fraud, default`, else the alphabetically last value | AI never asked | code wins. Also used by `dashboard._measure`, `insights.key_metrics`.
19. `derived.rules_ratio`: numerator by word overlap with the KPI name, denominator by "part of" + largest median | AI `ratios` missing | AI first, then second AI call, then code.
20. `derived.check_ratio` and `derived_check.judge`: stat checks (rho <= 0.8, 50x scale, >=95% positive denominator, 20x total, low confidence rejected for non-mul) | AI proposals | code wins and the verdict is logged.
21. `derived_check.judge` accepts `div` / `sub` / `add` proposals but nothing builds them (only `mul` is read, by `retail_roles._ai_product`) | AI proposed, code "accepted" | accepted proposals are dropped silently.
22. `goal.asks_about_change` | brief `intent` | AI wins when it answered; regex `CHANGE_RX` otherwise.
23. `context.detect_industry` keywords | brief `industry` | AI wins when it answered; keywords otherwise.
24. `polarity.validate`: names containing id/code/zip/postal/phone/index/row/lat/lon are forced neutral; blank AI answers filled by `heuristic` | AI direction | code wins for id-words; "price index" or "discount code" cannot be higher/lower even if the AI says so.
25. `summary.group_columns`, `named_in_goal`, `mentions`, `goal_pick`, `rules_pick`: which columns the goal "names" and which finding answers which question is word overlap | AI `none` or empty for a question | in `summary.pick` an empty AI list falls through to the rules, so the rules overrule an explicit AI "none".
26. `insights.data_gaps` / `kpi_found`: "KPI not found" decided by token overlap with column names when the brief gave no column | AI mapped the KPI to `null` | a name overlap can hide the gap the AI reported (code wins).
27. `insights.build_insights`: confidence labels, score, "KPI related" flag from tokens | AI | code only. Chart type is fixed per tool (`CHART_CHOICE`).
28. `tiering` / `reporter.split_insights`: evidence vs appendix vs checked, strategy merge by (theme, tool) template | AI | code only.
29. `dashboard.build_dashboard`: measures (first 3), dimensions by eta-squared >= 0.02 (max 4), date column | AI | code only; the AI is never asked what to plot.
30. `relations.detect_relationships` / `choose_base`: join keys and base table | AI veto, and the AI's base suggestion | code wins; the prompt does not even ask for `base_table`, so the branch in `graph.relate_node` that reports an ignored reviewer base is dead.

---

## 3. Same decision made twice

| Decision | First maker | Second maker(s) | Notes |
|---|---|---|---|
| Is this column an id / code | profiler `ID_NAME`, `_numeric_role`, `integer_code_like` | brief role `id`; `brief._id_like`; `retail_roles._number_columns`; `derived_check.is_id`; `planner.id_like_columns`; `polarity.ID_TOKENS` / `NEUTRAL_TOKENS` | six independent rules plus the AI; results can disagree between stages |
| Additive or rate | brief `kinds` (AI) | `measures.measure_kind` name regex; `retail_roles` value tests; `polarity.NEUTRAL_TOKENS` (quantity words) | different rules answer different callers |
| Is it money | `measures.MONEY_COL` | `MONEY_COL_NARROW`; `insights.FINANCIAL`; `wording.FINANCIAL` (same regex, two copies) | |
| Is this a quantity (count) | brief role `measure` | `retail_roles.is_quantity`, `is_count_like`; `profiler._numeric_role` (<=8 ints = category) | |
| Which column is the KPI | brief `kpis` (AI) | `context.kpi_tokens`, `planner.heuristic_plan.evidence`, `insights.build_insights`, `insights.data_gaps`, `derived.rules_ratio` | AI map used when present, token match elsewhere |
| A ratio KPI's two parts | brief `ratios` (AI) | brief retry (AI), `rules_ratio` (code), `derived.propose` (separate AI prompt), brief `derived_measures` op `div` | four routes to a ratio; the last never builds |
| Which date | `clarify` multi_date question | `planner` first date column, `named_in_goal`, `ov["date_col"]` | |
| Target / main number | brief `already_total`, product measure | planner AI target, `heuristic_plan.evidence`, user answers | three authors, override logic in `finalize_plan` |
| Hierarchy | brief (AI, checked) | `relations` FK graph (tables); none for columns by code | single owner for column hierarchy |
| Asks about change | brief `intent` | `goal.CHANGE_RX` | AI first |
| Industry | brief `industry` | `context.detect_industry` | AI first |
| Which finding answers a question | AI id list | `goal_pick`, `rules_pick` | rules run per question when the AI list is empty |
| Yes/no flag values | `signals.flag_values` (`YES_WORDS` / `NO_WORDS`) | `tools.to_binary` (`pos_words`) | two vocabularies, different words |
| Placeholder words | `profiler.NA_TOKENS` (silent NaN) | `signals.PLACEHOLDER_LABELS` (asks) | overlap means the question never fires for shared words |
| Placeholder numbers | `placeholders.detect` | `signals.sentinels` (`CONVENTIONAL_NUMERIC`, zero/edge spikes) | two detectors, two question families |
| Group columns | `planner.rank_groups` (assoc_strength) | `summary.group_columns`, `dashboard` eta-squared | three rankings of the same thing |
| Polarity | AI | `polarity.heuristic`, `insights.polarity`, `_forced_neutral` | AI first, regex fills |
| Number validation | `insights.numbers_ok` | `reporter.llm_summary`, `strategy.refine_with_llm` use it; `wording` has its own claim regexes | consistent but numbers only |

---

## 4. Things the AI is never asked to do but should be

1. **Spot odd data.** Oddities come only from fixed signals. The AI never sees values beyond 3 samples, so it cannot flag impossible values (negative age, ship before order, future dates, mixed units/currency), inconsistent spellings (NY vs New York), test/void rows (apart from the flag question), or "this column is a code" beyond role.
2. **Decide calculations.** The only calculation it can propose is a two-column op in the brief. Growth, share, per-customer averages, contribution, cohort/retention, YoY, weighted averages are fixed in code (`ranking_facts`, `_growth_fact`, tools).
3. **Choose what to plot.** It picks tools; chart type, series, top-N and the dashboard are code (`CHART_CHOICE`, `dashboard`).
4. **Say what a missing value means.** Median fill, "Unknown", and NA-token NaN are code. "None" may be a real category.
5. **Pick the positive label of a yes/no column** (`to_binary`).
6. **State the grain of a row** ("one row = one order line") and which column is the primary key.
7. **Choose time granularity and which period to compare** (day/week/month; first vs last full period is hard-coded in `GROWTH_UNITS`).
8. **Judge practical importance.** Effect-size cut-offs, `DOMINANT_SHARE`, `TRIVIAL_SHARE`, 30-row group limits are constants.
9. **Review claims, not just numbers.** No stage asks the AI (or code) to verify that wording names the right group or direction.
10. **Design an analysis for an unanswered goal question.** `summary.answers` says "Not answered" instead of asking the planner for a new analysis.
11. **Write a holistic "what looks wrong with this data" section.** Data gaps are regex on column names; the critic's AI call sees finding text only, not the profile.
12. **Name segments that matter.** Group choice is `assoc_strength` ranking plus words in the goal.

---

## 5. Known bugs and risks (ranked, not fixed)

**High (wrong numbers or lost run)**

1. `profiler.ID_NAME` has `[a-z]Id$` compiled with `re.I`, so it matches any word ending in "id" after a letter: Paid, Unpaid, Prepaid, Valid, Void, Android, Liquid. `Amount Paid` can be classed `id` (`_numeric_role` when unique share > 90%), is demoted from measure to `id` by `brief._make_validator`, and is excluded by `profiler.id_like` from `retail_roles._number_columns`, `derived_check.judge`, `brief._numeric_measures`. A money column silently vanishes from KPIs, ratios and derivations.
2. `cleaner.scoped_clean`: median imputation (<=30% missing) of every relevant numeric column except `plan.target`. KPI columns named in the brief, ratio numerators/denominators and other additive measures are imputed, so totals, ratios of sums, rankings and `key_numbers` are computed on invented values. Only a log line and a critic "info" (>10% of rows imputed) mention it.
3. `graph.insights_node`, `strategy_node`, `report_node`, `powerbi_node` and `critic_node` have no error isolation (only `analyze_node` catches per tool). An exception in `summary.ranking_facts`, `tiering.build_view` or `reporter` ends the run as `error` after all analysis work; `result.json` is written only at the end (`runner._execute`), so nothing is saved.
4. `planner.finalize_plan` (lines 536-538) with `graph.plan_node` (365-366): when the override target (including the brief's AI `already_total`) differs from the AI planner's target, the whole AI plan is replaced by the rules plan. `propose_plan` still sets `plan["source"] = "llm"` and `stage_sources["planner"]` stays AI, so the run is labelled "AI-assisted" with a fully rule-built plan. The note blames "you".
5. `profiler.normalize_strings` / `NA_TOKENS`: "none", "missing", "undefined", "-", "?" in any text column become NaN silently, then `scoped_clean` relabels categorical blanks "Unknown". A real category such as `coupon = None` is lost; group tables show "Unknown". The sentinel question for those words can never fire (`signals.PLACEHOLDER_LABELS` runs after conversion).

**Medium**

6. AI wording is checked for numbers only (`insights.unverified_numbers`, `reporter.llm_summary`, `strategy.refine_with_llm`). A model can name the wrong group, flip direction or claim cause, with every figure legitimate. The allowed set includes any number in the whole payload and context; `_matches` also accepts x100 and /100 and K/M scaling, so "50" passes if 0.5 appears anywhere.
7. Unbounded prompts: `reporter.llm_summary` sends **all** ranking facts (growth facts list every group, up to 50 per fact) and `summary.llm_pick` sends the whole catalogue. Estimated 5k-20k tokens; over a free-tier per-minute limit the call fails, `ask_json` returns None, and the report silently becomes rules-only. The log says only "bad JSON".
8. `derived_check.judge` accepts `div` / `sub` / `add`, logs "accepted by code", and nothing builds them. A `mul` is built only via `product_question`; when `_rate_basis` says "total" the signal is skipped and the AI's `mul` is silently dropped.
9. `tools.to_binary` picks the alphabetically last label when no known word matches, and `profiler._numeric_role` calls any 2-valued numeric column `binary`. A two-tier price or 0/5 discount becomes an outcome rate; "Completed/Cancelled" and "Paid/Unpaid" get arbitrary positives. Feeds `group_compare`, `trend`, `dashboard`, `key_metrics`, target selection.
10. `summary.pick`: `chosen.get(i)` is falsy for an AI `["none"]`, so rules (`goal_pick`, `rules_pick`) answer the question anyway. Word-overlap matching can answer "revenue by region" with a "profit by state" ranking.
11. `retail_roles.demote_quantities` in rules-only mode: class (1,2,3), grade, month number, small integer codes become numeric and are never grouped (`group_columns`, planner roles). With an LLM, only an explicit `dimension` rescues them.
12. `clarify.cap_questions` + `placeholders.question`: placeholder default is "missing" (data-changing). A capped placeholder question runs its default with no user input, against the stated rule that skipping never touches data. A top-coded legitimate maximum is exposed to this.
13. `planner.heuristic_plan` (lines 310-324): target candidates are not filtered by `id_like_columns`, and with zero evidence the fallback is the first numeric column ordered by least-missing. Can pick a code/ID-like or quantity column as the main number in rules-only runs.
14. `measures` and `polarity` regexes: `SUM_WORDS` contains `count` (matches country, account, discount), `AVG_WORDS` contains `age$` (usage, image, package) and `rate` (corporate, separate); "discount_amount" and "total_price" resolve to rate. `polarity.LOWER_STEMS` uses `startswith`: "latest_*" and "returned_customers" are lower-is-better. Only bites when the AI did not answer for that column.
15. `critic_node` / `route_after_critic`: after the one follow-up round the critic runs again, including a second LLM call whose <=2 follow-ups are never run (`iteration > MAX_ITERATIONS`) but remain in `critique["followups"]` with `approved=False`. Critic follow-ups are validated against raw roles: no `demote_quantities`, no user exclusions, no id check.
16. Resume path: on resume `relate_node` runs again (its `llm_review` is non-deterministic at temperature 0.2) while `brief.json` and `questions.json` are reused. In multi-table runs the joined table can differ from the one the brief and questions were built on.
17. `planner.plan_validator` rejects the whole AI answer if one step is bad (then repair, then rules plan), up to 4 invokes. `finalize_plan`'s "Dropped a proposed step" branch is unreachable for AI plans. One typo in 14 analyses costs the AI plan.
18. `clarify.llm_questions` calls `ask_json` directly: no repair call, no decision-log entry, no `stage_sources` entry; the writer's output is invisible in the run-mode label.

**Low**

19. `llm.get_llm`: no request timeout or `max_retries` set on any provider client; a hung provider hangs the worker thread. `_last_error` is a module global shared by concurrent runs (wrong "why" in the log). The provider is cached for the process lifetime.
20. `propose.derive_mode` counts only brief, planner, polarity, insights, strategy, report. Clarifier, critic, `derived.propose`, `summary.pick` are not counted, so "AI-assisted" can describe a run where those fell back.
21. `graph.brief_node` line 237: `stored.get("decisions") or [stored["decision"]]` raises KeyError if a cached brief has an empty decisions list.
22. `derived.propose` (plan_node) is a second, uncached, unlogged AI call that overlaps the brief's ratio path and runs even when the brief already supplied ratios.
23. `signals.sentinels`: legitimate values 99/999 (scores, ages) flagged as strong "conventional" sentinels and consume question slots (default keeps them, so low harm).
24. `coerce_types` runs three times per run (clarify, plan, clean) on the same raw table: wasted time, and three chances for divergent results if inputs differ.

---

## 6. AI calls and token size per stage (typical run)

Typical run: one table, ~20 columns, a goal and business context, a provider available, 3 ranked KPIs. "In/out" are estimates in tokens. A "call" means one `ask_json` (which itself makes up to 2 HTTP invokes on a transport error, `llm._invoke` retries=1 with a 2 s sleep).

| Stage | Calls (typical) | Worst case | Prompt in / out (est.) | Falls back to rules when (exact condition) |
|---|---|---|---|---|
| Relate (`relations.llm_review`) | 0 (single table); 1 for multi-table with links | 2 | 0.6-1.5k / 0.1k | `llm is None`; `ask_json` returns None; missing fields are ignored. Never raises. |
| Brief (`brief.resolve` via `propose`) | 1 | 2 (repair) + 2 (ratio retry, only when a ratio-worded KPI got no ratio) | 1.8-2.5k / 0.8-1.0k (80 columns: ~5k / 2.5k) | `llm is None` -> `rules-only`; JSON invalid, contains any numeric JSON value, or any validator error, twice -> `fallback` (`_empty()`: no roles, kinds, KPIs, ratios, hierarchy, derived). Cached in `brief.json` (not repeated after a pause). |
| Clarify writer (`clarify.llm_questions`) | 0-1 (only if non-code-owned signals exist and no `questions.json`) | 1 | 2.5-4k / 0.6-1.2k | `llm is None`; reply not a dict or no `questions` list; or every returned question invalid while the list was non-empty -> templates. An empty `questions: []` is accepted as "none". |
| Plan: derived ratios (`derived.propose`) | 1 (needs >= 2 measures) | 1 | 0.7k / 0.15k | `llm is None`; < 2 measures; reply not a dict / no `metrics` list -> `([], [])`. No repair, not logged. |
| Plan (`planner.propose_plan` via `propose`) | 1 | 2 (repair) | 3-4k / 1.2-1.5k (80 columns: 6.5k / 1.5k) | `llm is None`; JSON invalid; numbers present; > cap analyses; any bad tool/column/role; < 2 good analyses; bad target/date_col role; twice -> `heuristic_plan`. |
| Analyst | 0 | 0 | none | per tool: exception -> "Skipped" (run continues); `None` result -> "not enough usable data". |
| Critic (`critic.review`) | 1-2 (second pass after follow-ups) | 2 passes x 2 = 4 invokes | 1.4k / 0.3k | `llm is None`; `ask_json` None. Deterministic checks always run. Invalid follow-ups dropped silently; contradicted notes dropped with reason. |
| Insights: polarity (`polarity.resolve` via `propose`) | 1 | 2 | 0.35k / 0.15k | `llm is None`; reply shape wrong; direction missing or not in synonyms (non-id names) twice -> `heuristic` per name. Partial answers: gaps filled by heuristic. |
| Insights: enrich (`insights.enrich_with_llm`) | 1 | 1 | 2.2k / 1.3k | `llm is None`; reply has no `insights` list. Per field: length bounds, `numbers_ok`, `unsupported_profit_claim`; failing fields keep the template text. |
| Strategy (`strategy.refine_with_llm`) | 1 (0 when no data-backed candidates) | 1 | 3.5k / 2.4k | `llm is None`; no data candidates; no `strategies` list. Per item: text bounds, `numbers_ok`; whole item reverted if it pushes both up and down. |
| Report: pick (`summary.llm_pick`) | 1 | 1 | 3-8k / 0.15k | `llm is None`; no questions or items; no `answers` list. Per question: empty or `none` -> rules. |
| Report: summary (`reporter.llm_summary`) | 1 | 1 | 5-15k+ / 0.4k | `llm is None`; `ask_json` None; missing `summary` string or non-empty `recommendations`; no summary sentence survives `unverified_numbers` / `unsupported_profit_claim` -> `rule_summary`. Dropped sentences make it `mixed`. |
| Cleaner, profile, powerbi, dashboard, tiering | 0 | 0 | none | code only |
| **Total (typical)** | **~10-11 calls** | **~32 invokes** (brief 8, plan 4, polarity 4, critic 4, six others 2 each, relate 2) | **~25-45k in, ~7-9k out per run** | |

Notes on the table:
- `llm.deterministic` (temperature 0) is applied to the brief only; every other call uses temperature 0.2, so planner and wording differ between identical runs.
- `propose._numbers` rejects any JSON number in a reply; digits inside strings are not caught.
- A paused run (needs answers) executes profile, relate (and its AI veto again), brief, clarify, then ends. On resume: profile, relate again, brief and clarify from cache, then plan onward. The paused portion is not re-billed except relate.
- Run-level label: `derive_mode(stage_sources)` over six stages (brief, planner, polarity, insights, strategy, report). Mixed-mode runs are normal: each stage can fall back independently.
- Provider limits matter at the two large prompts (report summary, summary pick). The estimates are from prompt sizes and have not been measured with a real provider.
