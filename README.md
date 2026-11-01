# Analyst Studio

**Upload a sales file, answer a few simple questions, get a short report you can trust.** The AI proposes; code does every calculation and every check.

![The Analyst Studio start page](docs/screenshots/start-top-desktop.png)

## The problem

A small online seller has a sales spreadsheet and no analyst. Charts from a spreadsheet are easy to make and easy to get wrong, and an AI chatbot that "reads" the file will happily invent a number. Analyst Studio is built for the person who needs a report they can act on and show to a partner, and who must not be misled:

- every figure in the report is **calculated by code on the whole file**, never written by the AI;
- every claim carries a **how sure we are** label (from effect size, multiple-testing correction, sample size and number of periods), and "no reliable difference" is said when that is the truth;
- anything the AI writes is **checked against the computed facts**; a number it cannot back is dropped and the rule-based sentence is kept;
- it **asks** before it assumes (for example what a value of 999 in a delivery column means), at most three questions;
- it runs with **no AI key at all** (rules-only) and still produces a complete, honest report.

![The report: the short answer, key figures and what to do](docs/screenshots/report-top-desktop.png)

## Try it

No key and no upload needed:

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm install && npm run build
cd ../backend && uvicorn app.main:app --port 8000      # open http://localhost:8000
```

Press **Try with sample data**. It opens a finished run of a small sales file (about 600 rows, a sample store): the report, dashboard, insights, ideas to try and the round **Lens** button, where you can pick a suggested question or change what a tab shows ("make the insights tab shorter"). Nothing calls an AI. To run your own file, press **Try it yourself**; add a free key (see *Pick a free brain*) for smarter wording, or use none. To run it in Docker, see *Deploy*.

More: [how it is built](docs/architecture.md) (pipeline and Lens diagrams, and why the AI proposes and code verifies) and [how it is checked](docs/evaluation.md) (scorecards, report rules, citation check, Lens against pandas).

## How it works

An agentic data analyst: a team of agents profiles the data, plans only what your goal needs, cleans it (with a full audit log), runs the statistics, challenges its own conclusions, and gives you a report plus a **Power BI starter pack**. The heavy lifting (profiling, cleaning, statistics, charts) is plain Python. The LLM only plans, critiques and writes prose, and it can be a free local model, a free-tier API, or nothing at all.

```
 React UI ──upload + goal──▶ FastAPI ──▶ LangGraph workflow
    ▲                                       │
    └───── live agent events (SSE) ◀────────┤
                                            ▼
   Profiler → Joiner → Brief → Clarifier → Planner → Cleaner → Analyst → Critic ──┐ (needs a follow-up check?)
                                                         ▲              │ yes
                                                         └──────────────┘
                                                            │ no
                                                            ▼
                                  Insights → Strategist → Reporter → Power BI pack
```

The stage order lives in `backend/app/graph.py`. Every stage is wrapped so that a failure falls back to the rules version (or is skipped) with a warning, and the run still finishes with everything that exists.

| Agent | Job | Uses LLM? |
|---|---|---|
| **Profiler** | Detects the role of every column (id, numeric, binary, category, date, text), including numbers/dates stored as text | no |
| **Joiner** | With 2+ files (or Excel sheets): finds the key links, picks the analysis grain from your goal, joins safely, and measures every join | optional (can veto links) |
| **Brief** | One LLM call per run: what each column means (id, measure, dimension, date, text), whether a measure is additive or a rate, which column each KPI you named is, and which ratios or line totals exist. Every part is checked by code; a part that fails is dropped and the rules decide | optional (rules fallback) |
| **Clarifier** | Finds ambiguities from the data alone (two dates, placeholder values such as 999 or -1 found on the source tables before joining, negatives, derived or duplicate columns), asks **at most 3** short questions, and applies the answers through a closed list of actions | optional (writes the questions; templates otherwise) |
| **Planner** | Turns the goal into questions, picks the target and only the relevant columns, and chooses analyses | optional (rules fallback) |
| **Cleaner** | Fixes types, standardises missing values, removes duplicates, imputes, flags outliers, **only for needed columns**, logging every change | no |
| **Analyst** | Runs the planned analyses from a tested toolbox (tests, effect sizes, charts) | no |
| **Critic** | Multiple-testing correction, small samples, skew, practical vs statistical significance; can send the Analyst back for robustness checks | optional |
| **Insights** | Turns each finding into a card: what we see, why it matters, how much to trust it, explanations to test | optional (rules wording) |
| **Strategist** | Ranked, testable strategies from significant findings only | optional (rules wording) |
| **Reporter** | Assembles the report in three tiers. The LLM only writes the summary and recommendations. Numbers come from the tools | optional |
| **Power BI pack** | Cleaned data, data dictionary, DAX measures, visual specs, theme and guide | no |

Anything an LLM proposes is validated against the real columns. Hallucinated tools or columns are dropped, and
unusable output falls back to the rule-based planner.

## Tell it about the business (optional)

Expand **Tell us about the business** on the upload form: company, industry, what it does, the KPIs you care about, who reads the results, currency, and any notes.
Everything is optional and treated as *unverified background*, never as fact about the data. It is used to:

- **steer the analysis**: a KPI such as "average handle time" makes that column the target; industry presets add sensible questions and segments;
- **sharpen the wording**: why it matters, possible explanations and strategies speak in your business's terms (rules-only mode uses the industry lens; an LLM adds tailoring);
- **flag gaps**: a KPI you named that is not in the data, or data an industry usually needs (for example no date column for trend questions).

Not sure what to look for? Tick **I'm not sure what to look for**: the goal becomes optional and the agents explore the data broadly (extra analyses, a second target, heatmaps and per-segment trends).
If an LLM is enabled, the context text is sent to it, so leave out anything confidential.

## Business glossary and clarifying questions

Before planning, the Clarifier looks for things in the data that would change what a number *means*, asks a few short questions (**3 at most**, ranked by how much the answer changes the analysis; the rest use their defaults and the run says so), and applies the answers. It works on **any tabular data**: the question logic contains no word lists about any business domain and never looks at what a column is called (a test scans the code for domain words).

```
data -> signals.py (neutral facts) -> question writer -> validator -> you answer or skip -> cleaner executes the chosen actions
                                       LLM rewords, or one generic
                                       template per signal
```

**Placeholders** (`placeholders.py`) are looked for table by table before any join, because a 999 that is joined first gets averaged into a summary and can no longer be found. A column the analysis uses gets one question (default: treat as missing); an unused column is set to missing and logged without asking.

**Signals** (`signals.py`, values and distributions only): two or more date columns; several candidate measures (never a question: the planner analyses every one of them, see below); negative values in a column that is mostly positive; a placeholder-looking value (-1, 9999, 'unknown'…) or a spike of one value; a column that is almost exactly another plus or minus a third; very rare labels; high missingness; two columns that look like duplicates. Each has a stable id (hash of type + columns), so a saved answer matches the same ambiguity on the next run.

**Every question is anchored to a signal.** A question must carry the id of a signal that `signals.py` found in this data, and its options may only do what that kind of signal calls for (a placeholder can be treated as missing, not 'drop another column'). The LLM can only *reword* questions for signals that exist; anything it invents with no backing signal is dropped, even if its options would change the data. On data with nothing ambiguous the app asks 0 questions, with or without an LLM.

**Several measures, segments, periods, total vs average are never questions.** All candidate measures are analysed (distribution, trend, average per record and totals by segment, and one overview table). Every segment column and every period the data supports is analysed and shown. Ratios between measures are never invented by rules; see *Derived metrics* below.

**Question writer.** With an LLM configured, it gets the compact profile, the signals, the goal, your business context and the saved glossary, and writes the questions. The prompt says: ask only when the answer changes what a number means; never ask which chart, grouping, column, time frame or view to show (the app analyses everything reasonable and ranks it); merge duplicates. With no LLM, or if its answer is unusable, one template per signal type produces the questions. A model that explicitly answers "nothing to ask" is respected.

**The closed list of actions.** Every option carries an action from a fixed list that *code* executes. Nothing an LLM writes is ever run.

| Action | Effect |
|---|---|
| `set_date_column` | the date used for time analysis |
| `set_measures` | the number(s) the analysis is built around |
| `define_derived_metric` | ratio of sums, or difference, of two measures (new column) |
| `filter_rows` | keep rows where `column <operator> value` holds (eq ne lt le gt ge in not_in is_null not_null) |
| `treat_value_as_missing` | placeholder values become missing in the analysis copy |
| `handle_negatives` | keep / exclude / net against another column (new column) |
| `drop_column`, `merge_columns` | leave a column out; combine columns (first non-null, or sum) |
| `note_only` | recorded as context for the LLM; the data is untouched |

**Validator.** Questions are dropped when they have no backing signal, mention columns that do not exist, use an action outside the list or with invalid parameters, or duplicate another question. The check is **structural, not word-based**: a question is kept only if at least one answer changes the data (filter, drop, merge, placeholder, derived column, negatives) or picks the date. Questions whose answers are all notes, or just alternative columns, views, groupings, aggregations or time frames the app analyses anyway, are dropped whatever they say. The default answer of every question must leave the data unchanged (otherwise it is rewritten, or the question dropped), so **Skip, use defaults never changes a number**. Weak signals (rare labels, missingness…) only ride along when something stronger already needs an answer, and a question already settled by your goal (e.g. it names a date column) is not asked.

**Flow.** If a question needs an answer the run stops with status `needs_input` (the graph ends; no thread blocks) and the UI shows a form (radio options, your own definitions, notes, **Continue**, **Skip, use defaults**). Posting the answers restarts the run without repeating timeline events. Runs with nothing ambiguous are never blocked, nor are runs where the saved glossary already answers everything.

**What the answers do.** Actions run in the cleaner before any total is computed. Nothing is changed silently: original values are never edited (new columns are added, rows are filtered in the analysis copy), every effect is in the cleaning log with the rows or values affected, and an action that would remove every row is skipped and logged. The report gets a **Definitions used** section: answer, set by (*You / Default / Saved glossary / Your goal*; for the glossary, which workspace saved it and when, because the app has no user accounts), effect, rows affected. A saved answer is ignored (and listed as not applied) when its question is no longer asked for the data or the saved option no longer exists, and it is never shown to the question writer in that case. Free text that maps to no action (your definitions, notes, `note_only` answers) is passed to the LLM as *unverified* context and listed in the report as "not applied to the data".

**Glossary.** Answers are saved in `backend/data/glossary.json` (override with `GLOSSARY_PATH`) keyed by company name (lower-cased; `default` when no company is given), together with the option's label so the Glossary page reads naturally. The next run with the same company pre-fills them. Limits: 500 characters of notes, 300 per definition, 20 definitions per company. Writes are atomic and lock-protected.

| Endpoint | |
|---|---|
| `GET /api/runs/{id}/questions` | The questions to answer, with pre-filled values |
| `POST /api/runs/{id}/answers` | `{answers, notes, definitions, skip}`; `409` unless the run is `needs_input` |
| `GET /api/glossary[?workspace=]`, `PUT /api/glossary?workspace=`, `DELETE /api/glossary?workspace=` | Saved definitions |

> **The glossary API has no authentication.** Anyone who can reach the server can read, change or delete saved definitions, like the rest of this local-only app.

![Glossary page](docs/screenshots/glossary-desktop.png)

## Derived metrics (ratios) are proposed, then checked against the data

A ratio you name as a KPI (for example "profit margin") is created by code whenever the Brief, or the rules as a fallback, find its numerator and denominator columns. Beyond that, rules alone never invent a ratio. With an LLM, the planner may propose up to 2 more ratios A / B, and each proposal must come with a one-line business meaning and then pass checks that look at **values only** (`derived.py`, `derived_check.py`, no column names, no domain words):

| Check | Rejects |
|---|---|
| The ratio is not almost entirely a function of its denominator | \|spearman(A/B, B)\| > 0.8 (for example units divided by an amount, which just reproduces the inverse of the amount) |
| The two columns are on comparable scales | typical sizes more than about 50x apart |
| An id or count-like column is not divided by a continuous magnitude | small whole-number counts or ids over decimals |
| The denominator is positive in nearly every row | unstable divisors |

"Part of" ratios (A mostly within 0..B, a component over its total) rank first, at most two are kept, and a ratio is always a **ratio of sums** (total of A over total of B, per group and per period), never the mean of row ratios. Every created metric, with its meaning, and every rejected proposal, with the reason, is listed under **Definitions used** in the report and on the dashboard, where **Remove and re-run** repeats the analysis without it (`POST /api/runs/{id}/remove-derived`).

## The goal gets a direct answer

If the goal asks whether something is growing, falling or changing over time, the planner puts the overall trend and the per-group trends first, and the report opens with **Answer to your goal**: one sentence with the direction, the size of the change and its significance (for example "There is no reliable growth trend in amount: it changed about -5% over the period, which is not statistically significant (p=0.58, 24 months)"), plus one on the groups, corrected for the number of groups tested. The dashboard shows that sentence with the two trend charts above everything else.

## The report has three tiers

`tiering.py` arranges the same findings; it computes nothing new.

| Tier | Content |
|---|---|
| 1. The answer | One line per goal question (or "Not answered"), key numbers, up to 5 headline findings, up to 3 actions. Ranking facts ("which X is most Y") and growth facts (every group's first full period against the last, marked reliable or not) are built from the cleaned data, so they exist even when no difference was significant (`summary.py`) |
| 2. Evidence | Up to 8 cards: findings named by the goal, or significant after correction with a medium or larger effect |
| 3. Appendix | Everything else, collapsed. Non-significant tests become one "Checked, nothing found" line each; distributions and shares go to a data profile |

Numbers in the prose are checked against the findings; a number the tools did not compute is rejected and the rule-based text is kept. If an AI call failed (rate limit, timeout, auth), the report says so instead of presenting rules text as AI text.

## Charts show a readable slice; statistics always use all the data

Only the picture is trimmed. Every test, effect size, best/worst group and total is computed on every row and every group, and every trimmed chart says so on the chart itself ("showing top 8 of 17 groups; statistics use all 17"), in the finding text and in its stats.

| Chart | What is drawn |
|---|---|
| Ranking bars (group comparison) | up to 8 groups: all. More: top 8 by the plotted value, always including the best and worst group named in the headline, plus one **Other (k groups)** bar (the pooled mean or rate of the remaining rows; a ratio of sums for derived metrics). Horizontal bars when there are more than 6 or the labels are long, with the value and n beside each bar |
| Share charts (category share, Pareto, composition) | top 7 plus Other |
| Per-group trends | the 5 biggest groups, always including the fastest and slowest; every group with enough data is tested and the p-values are corrected for all of them |
| Heatmaps | the 6 x 6 biggest rows and columns, always including those of the best and weakest cell, which are found over all cells |
| Histograms | axis clipped at the 99th percentile with the number of values beyond it stated; a log axis for extreme skew |
| Tables | 10 rows, **Show all N** |
| Dashboard | the goal answer first, then the four highest-scoring findings; the rest under **More charts** |

![The dashboard, on the sample report](docs/screenshots/tour-dashboard-desktop.png)
![The report page: the short answer, key figures and what to do](docs/screenshots/report-top-desktop.png)

## Insights and strategies

Every chart comes with an insight card: a headline, **what we see**, **why it matters**, **how much to trust it** (confidence from effect size, multiple-testing correction, sample size and number of periods),
**possible explanations to test** (explicitly not conclusions) and **what to check next**. Descriptive charts (shares, distributions, Pareto) are labelled descriptive rather than "low confidence".

The **Strategy** tab turns only *significant, correction-surviving* findings into ranked, testable strategies: objective, actions, how to pilot it, KPIs to track, risks, impact, effort and time horizon, plus an impact/effort matrix.
If nothing in the data supports a lever, it says so ("No strong, data-backed lever was found") and offers foundation steps (close data gaps, track the KPI in Power BI) instead of inventing advice.
LLM output can only reword these; any number it introduces that is not in the findings is rejected and the rule-based text is kept.

## The dashboard, data viewer and ER diagram

- **Tabs:** Dashboard, Insights, Strategy, Report, Data, Data model, Cleaning log, Plan, Power BI. A History view lists the 20 newest runs.
- **Dashboard** (first tab): headline number tiles, the top recommendation, the two most important charts large, then every chart grouped by the question it answers (what drives the result, over time, shape of the data, tables). Filter by high confidence or your own KPIs; click any chart for its full insight card and *why this chart was chosen*.
- **Data**: browse the cleaned table the agents actually used (search, sort, paginate), switch to the original, see per-column *what cleaning changed* (type, missing values), and download the cleaned CSV.
- **Data model**: for several files, an ER diagram drawn from what the Joiner agent found: tables with their columns, key columns highlighted, 1 / N ends on each link, solid lines for links used, dashed for links skipped or rejected. Hover a line for the match rate and reason.

## Charts and tables the agents can choose from

Sixteen tools in all (`tools.py`): distribution, outliers, category share, group compare, outcome compare, robust compare, Pareto, trend, correlation, heatmap, segment trend, scatter, composition, seasonality, summary table and group table. As charts: histogram, box plot, bar chart with error bars, Pareto, trend line, multi-line segment trends, weekday/month seasonality, correlation heatmap, two-way heatmap, scatter with fitted line (zooms past extreme points and says so), 100% stacked composition bars, plus a numeric summary table and a per-group table. The planner picks by column types and the goal; each chart records why it was chosen. An LLM, if enabled, can only choose from this list (anything invented is dropped).
Month-of-year seasonality is only claimed with at least two years of data, because with less it cannot be told apart from a trend.

## Several files? They get connected

Upload up to 8 files (or one Excel workbook with several sheets; each non-empty sheet becomes a table) and the **Joiner** agent:

1. **Finds links**: a key must be unique on one side and its values must appear on the other, with name evidence (`orders.customer_id` → `customers.customer_id`). Keys are normalised (`101` = `101.0` = `" 101 "`), and coincidental overlaps (two tables that both have `id` = 1..N, small numbers vs ids) are rejected.
2. **Picks the grain from your goal**: *"why do customers churn?"* → one row per customer; *"which segment drives revenue?"* → one row per order.
3. **Never multiplies rows**: many-to-one tables are joined in (orders get the customer's segment); one-to-many tables are **summarised first** (each customer gets `orders_count`, `orders_revenue_total`, `orders_days_since_order_date`, ...). Foreign keys are never averaged.
4. **Measures every join**: match rates, orphan rows, duplicate keys. A link where under 50% of rows match is skipped and reported, not forced.
5. **Shows its work**: the *Data model* tab and the report list tables, links, join steps and warnings, and the Power BI pack includes the original tables plus `relationships.csv` for native modelling.

Try `sample_data/shop/` (customers + orders + products) with *"Why do customers churn and which segment drives revenue?"*.

## Try it without uploading anything

Press **Try with sample data** on the start page. It opens a finished run of a small sales file (`sample_data/demo/sales_run/`, about 600 rows, a sample store): the same report, dashboard, data viewer, cleaning log and plan as a real run. The sample is **read-only**, makes **no AI calls** and needs no API key. **Try it yourself** opens a panel with the upload flow.

The start page is a product page: a headline, two buttons, then a framed window running the real app on the sample. Its tabs are the app's real tabs: **Report**, **Dashboard**, **Insights**, **Ideas to try**, **Your data**, **What we cleaned** and **How we analysed** (plus **How files connect** and **Power BI export** on runs that have them). Below it are a four-step "How it works" flow, a scroll story whose window follows the text, three trust statements, and what it can and can't do. A click on a tab overrides the scroll until the next step; with reduced motion the story is a plain list.

- **The report page** has the title, date and a "How sure we are" label; the short answer in large type; up to four key figures; **What to do** as numbered cards; **The evidence** (at most six charts, each with a one-line "What this means" and a Details toggle); and **Behind the numbers**, where everything else is folded away. The footer shows the mode (rules only, or AI-assisted).
- **Print / Save as PDF** includes or leaves out the summary and actions, key numbers, charts, tables, data-quality notes and evidence; sets the title, company, date and an optional note; and chooses A4 or Letter, portrait or landscape, colour or black and white. Your last choices are remembered in this browser only.
- **One look**: colours, six type sizes and the 4-point spacing scale are in `frontend/src/tokens.css`; the 1120 px / 720 px column and 12-column (4 on a phone) grid are in `layout.css`. Charts, tables and the dashboard share one style: the accent for the highlighted bar, warm greys for the rest, numbers right-aligned.

## Checking a report

`python scripts/report_check.py <run id>` reads a finished run and prints PASS or FAIL for seven rules: the recommendation lists are identical wherever they appear; no action is a bare fact; a "biggest" claim names the computed top group; no investment advice from size alone; the coverage warning appears exactly when under 70% of calendar days have data; no internal column names in the main view; and no hash links. The sample run passes all seven. Its tests use one good and one bad sample per rule.

Each run also keeps `ai_usage.json`: every AI call with its stage, input and output tokens (flagged as estimated when the provider gives no count), whether it was answered from a saved result, and its time. The report's data section has one line with the totals, and `scripts/scorecard.py` prints a per-stage table.

## Lens: ask your data

A round **Lens** button sits at the bottom right of every tab of a finished run. It opens a panel docked on the right (a bottom sheet on a phone), so the report stays readable beside it. **Expand** opens the same conversation as a full-width **Chat** tab; the two share one conversation. The conversation, its pins and the question count are saved with the run (`lens.json` in the run folder), so a reload brings them back.

![Lens on the sample report: the panel beside the report, with an answer, its chart and how it was calculated](docs/screenshots/lens-answer-desktop.png)

**The AI chooses, code computes, every figure comes from code.** For each typed question the AI gets the data understanding record (column meanings, roles, display names, units), the date span of each date column, the name of the tab you are on and the last 6 turns, never rows and never values. It returns a structured query from a closed list: *aggregate* (sum, average, count, count distinct, with filters, group by and split by), *top or bottom N*, *share of total*, *compare two groups*, *change between two periods*, *trend over time*, *distribution* and *relationship between two measures*. Code then:

1. **checks** the query: the columns exist, their roles fit the operation (nothing numeric is grouped, nothing categorical is added up), values and periods exist in the data. A value the user named ("delhi") is matched to the real value; one that is in two columns becomes a clarifying question. A query that fails is rejected with a plain sentence (the AI gets one corrected try);
2. **runs** it on the whole cleaned table and counts every row it used or left out;
3. **writes the sentence** from the computed numbers (each figure is recorded, so what is shown can be compared with what was computed) and **draws the chart**.

The AI never writes a sentence that is shown as a result. Its only free text is a clarifying question with 2 or 3 choices (each choice is a complete query, so choosing one needs no further AI call), and, for an unsupported question, one short reason (dropped if it contains a number).

- **Each answer** is one plain sentence, the chart or table inside the message, and a **How this was calculated** toggle (the operation, the columns, the filters, rows in the data / used / left out and why, groups not shown). Each chart has **Expand**, **Pin to report**, **Pin to dashboard** and **Download image**. Pins appear in a section **Added by you from Lens** on the report page or the dashboard; they are copies kept in `lens.json`, the original report is never changed, and each can be removed.
- **Chart controls** in plain language: chart type (bar, line, stacked bar, share, histogram, scatter, table), time grain, top N, sort, filters and comparing periods. Say it ("make it monthly", "as a line", "top 5", "and for Delhi?") or use the **Change the chart** buttons under an answer; the buttons are applied by code with no AI call. A chart that does not fit the result ("scatter" for a total by city) gets a plain note and the sensible chart instead.
- **What to ask next**: after each answer, 2 or 3 suggested questions written from this run's own columns and values, each carrying its complete query.
- **Unsupported questions** are said plainly, with two questions that can be answered. If margin or profit is asked and the data has no cost column (and no profit or margin column), Lens says that margin can't be computed because the data has no cost column, before any AI call.
- **No-AI mode** (the sample report, or no key): the suggested questions map straight to queries; typed text is answered only when it IS a suggestion (same words, or a typo away), otherwise the suggestions are offered. Chart controls still work.
- **Cost**: Lens uses the same rate-limit handling and AI cache as the rest of the app, at most **2 AI calls per question** (the second only repairs a reply that was not JSON or a query that failed the checks), and a per-run cap of `LENS_MAX_QUESTIONS` (default 50) questions that use the AI. At the cap Lens says so; the suggested questions and chart controls still work. A per-day provider limit stops AI questions for ten minutes, says so, and then tries again.
- **Flag** `CHATBOT=auto` (default): on when an AI model is available, and always on for the sample run. `CHATBOT=on` shows it on every report (suggestions only without a model); `CHATBOT=off` hides it. The sample run never calls a model, even when one is configured.

The code is in `backend/app/lens/`: `context.py` (what the AI may see), `query.py` (the closed list and the checks), `engine.py` (the calculations and sentences), `render.py` (charts), `chips.py`, `chat.py` (the only place that talks to the AI), `store.py` (saved state). A new operation is one function in `engine.RUNNERS` and one entry in `query.OP_HELP`. `python scripts/lens_kaveri_check.py` asks the real model eight Kaveri questions and compares each answer with pandas (`scripts/lens_kaveri_check_out.txt`); `node scripts/lens_screenshots.js` drives the panel in a browser.

### Lens, Stage 2A: the report and the app

Lens also answers questions about the report itself. With each question it is given the current tab, the report's checked fact list (with fact ids), the insights, the strategies, the cleaning log, the definitions used and the understanding record (`lens/records.py`), never rows. The AI chooses what kind of answer it is and the framing words; code quotes, ranks and checks.

- **A premise the data contradicts is corrected first.** "Why is Electronics the top?" when Clothing is top gets "Correction first: Electronics is not the top category by total amount: Clothing is, at ... Electronics is second, at ...", with the computed figures, and then the rest of the question is answered.
- **"Why" questions are not refused, and no cause is ever claimed.** Lens says plainly that the data alone can't show why, then shows what is behind the number, calculated by code and drawn: the share of the total, a split by the most relevant other column (the one whose groups differ most), or what changed between two periods. Two follow-up questions come from that breakdown.
- **Fewer groups than asked for** ("top 10 cities" with 4 cities) says "There are only 4 cities".
- **Refusals** now offer 2 questions that fit the topic and were not just offered. A request to change how the app looks ("dark mode", "switch the layout") is declined plainly, and the view changes that do exist are offered as chips (see *View edits*).
- **How this was calculated** is written once on the server (`lens/howtext.py`) and is never empty: the operation, columns, filters, rows in / used / left out for a calculation; the sources and fact ids for an answer quoted from the report.
- **App and report questions** ("what does this tab show?", "why is trust Medium?", "what did you clean?", "which definitions did you use?") are quoted from the run's saved records, with the toggle showing which record.
- **Summarise / shorten** gives at most 5 lines (3 for "shorten") plus the report's top 3 actions. The AI words the lines; each must state a figure and cite the fact it came from, and goes through the report's own citation checks (`cited.verify`: the figure must be the fact's, near the fact's subject; no causal wording); a line that fails is dropped, and if fewer than 2 survive (or there is no AI) the report's own sentences are used. **Use as my short version** keeps it as an alternative view in `lens.json` (shown on the report page as "Your short version"); the original report is never changed.
- **"What matters most for me?" / "I care about returns"**: the AI picks the framing words; code ranks the report's existing insights and actions by how closely they match and shows the top 3 with their figures. Nothing relevant is said plainly. With no priority named, Lens asks.
- **Strategy questions** reuse the report's strategies and their checks (`strategy.blocked`): labelled **Suggestion**, or **No suggestion: <reason>**.
- **General questions** not about the data are answered briefly and labelled "General knowledge, not from your data"; a sentence with a figure is dropped.
- After every answer, 2 or 3 **what to ask next** chips. Plain-word questions (summaries, app questions, view changes) and all chips run with no AI; the summary then uses the report's own sentences.

Cost is unchanged: at most 2 AI calls per question, the AI cache and rate-limit handling, the per-run cap. `python scripts/lens_stage2a_check.py` asks the real model nine Kaveri questions and compares each answer with pandas or with the saved report records (`scripts/lens_stage2a_check_out.txt`). New code: `records.py`, `reports.py`, `reasoning.py`, `howtext.py`; tests in `backend/tests/test_lens_stage2a.py`.

### Lens, fix pack (from real testing)

- **Split by**: "the largest single combination" is now the largest of *all* combinations. Before, only the 6 most common split values were kept, so for sub-categories nested inside categories a whole category (Furniture) could be dropped and a smaller combination named the largest. A nested split now shows up to 12 columns, and the column holding the largest combination is always shown. `engine.drawn_vs_table` compares the values a chart is drawn from with the computed table; the tests use it for every split-by answer.
- **Negative values**: a negative group is named ("Electronics has negative profit; shares can add up to more than 100%"), shares are shown even when they pass 100%, a negative slice is never drawn as a pie, up to 8 groups are all listed (negative ones marked) instead of "and N more", and the chip **Show groups that lose money** appears when some group is below zero.
- **"This graph is wrong"** re-checks the most recent chart (draws nothing new, compares the drawn values with the computed table, says the result in plain words, and says what may look odd, such as an incomplete last month). It asks which chart only when none was drawn.
- **View changes** ("make the ideas tab shorter", "simplify this tab", "remove this chart") are never turned into a summary: they are view edits (see *View edits*).
- **Unusual changes** (`unusual_changes`): the change from each period (month by default) to the next, flagged when it is more than 3 median absolute deviations from the typical change (and at least 10%), or at least 40%, with at least 5 rows in both periods. Partial periods are left out; flagged points are marked on the line chart and listed with their figures; if none, Lens says so.
- **Premise checks** now also catch "it should be X", "X is higher than Y" and "sales went down in March", by computing them; the correction and its figures come first.
- **Summaries** are one plain sentence per line, at most 20 words, no column labels or repeated words, and still cite and pass the figure checks. **Why did you choose it?** explains a suggested idea from the report's own ratings (impact, confidence, effort, the fact it rests on) and names the runner-up and why it ranked lower.
- A chart's alt text no longer repeats the answer sentence above it.

Tests: `backend/tests/test_lens_fixpack.py`. `python scripts/lens_fixpack_check.py` asks the real model eight Kaveri questions and compares each answer with pandas.

## Deploy

One image runs the backend and the built frontend together on port 8000. Nothing here needs a real AI key: without one the app runs rules-only.

```bash
cp .env.example .env          # then edit .env (it is git-ignored; never commit a key)
docker compose up --build     # open http://localhost:8000
```

1. **Settings.** Every setting is listed, with a comment, in `.env.example`. Leave `GROQ_API_KEY` empty for rules-only mode; add your own key in your private `.env` only (or set it as a secret in your host's dashboard).
2. **Data.** Runs, uploads, the glossary and the saved AI replies live in the `analyst-data` volume (`/app/backend/data`), so they survive a restart. `docker compose down -v` deletes them.
3. **Protect a public copy.** Set `UPLOADS_REQUIRE_CODE` to a code you share: uploads, AI runs and typed Lens questions then need it (entered on the upload panel), while the sample report and demo stay open. `DAILY_AI_RUNS` and `DAILY_LENS_QUESTIONS` cap the AI use of all visitors per UTC day (a friendly message is shown when reached; suggested questions keep working). `UPLOAD_RATE_LIMIT` per `UPLOAD_RATE_WINDOW_S` limits one visitor's uploads. Behind a reverse proxy you control, set `TRUST_PROXY_HEADERS=1` so visitors are told apart by `X-Forwarded-For`.
4. **HTTPS.** Put a reverse proxy (Caddy, nginx, or your host's load balancer) in front of port 8000 and terminate TLS there. The app itself has no user accounts.
5. **Free-tier limits.** `AI_TPM_LIMIT` spaces AI calls to fit a per-minute token window (default 6000 when the provider is Groq), so fewer calls are rejected and retried.
6. **Without Docker:** follow *Quick start* below, with `backend/.env` copied from `.env.example`.

### Lens as an agent: plan, act, verify, answer

Typed requests (with an AI model) go through one loop, `backend/app/lens/agent/`:

1. **Context**: the last 6 turns, the tab, the visible items with ids and titles, the focused item, the understanding record, the saved definitions, the report's fact sheet, insights, strategies and cleaning log. Never raw rows.
2. **Plan** (one AI call): a JSON plan of at most 4 steps, each naming a tool and its arguments. Code validates it against the tool registry (the tool exists, the arguments fit its schema, the ids and columns exist). An invalid plan gets one repair attempt, then the rule router (the no-AI fallback), then "here is what I can do" with chips; a request no tool can do is logged to `lens_unmet.jsonl` (request, tab, time; no data values).
3. **Act**: steps run in order, later steps can use earlier results, and the first failure stops the run and says what was done.
4. **Verify**: a sentence the AI wrote (a framing reply, a brief's lines) is kept only if every figure comes from a tool result or the fact sheet and passes the fact-id citation check.
5. **Answer**: each tool phrases its own result in code, so there is normally no separate wording call; "How this was calculated" lists each step and tool; a view change is one Undo for the whole request.

Normally at most 2 AI calls per request (custom analysis may use 2 more to repair its query; each brief adds one writing call). Chips, `undo`, and hide or show by exact title skip the plan call. The AI cache and the per-run question cap still apply.

**Tools** (one module each in `agent/tools/`, listed in `tools/__init__.py`; adding one needs a new module and one line): `data_query` (the closed operations, pre-verified, preferred), `custom_analysis`, `report_facts`, `rank`, `strategies`, `explain`, `summarise`, `glossary`, `general_knowledge`, `chart_edit`, `view_edit`, `brief`, `brief_manage`, `export`, `clarify`, `premise_check`, `recheck_chart`.

- **Custom analysis** is for what the closed operations cannot express. The AI writes one SQL query over a read-only view of the cleaned table; code **parses** it (sqlglot): one SELECT only, only that view, only known columns, no DDL, DML, PRAGMA, ATTACH, COPY, INSTALL, LOAD, file or network functions; the SQL that runs is regenerated from the parsed tree. DuckDB runs it in memory with external access off, a memory cap, a 10 s timeout and a row cap. An error is sanitised and given back to the AI for up to 2 repairs. The AI never receives rows: an aggregated result it may see is capped at 50 rows and 12 columns, and a query that is not aggregated shows at most 20 rows to the user only. The result becomes a fact with an id; when a closed operation is given for cross-checking, a disagreement is shown as "Two methods disagree". The answer is labelled "Custom analysis"; the SQL is collapsed under "How this was calculated". Without an AI it is unavailable, and Lens says so.
- **Briefs** for any reader, in free text, with several in one request: the AI infers what each reader cares about and returns a brief plan (audience, length, focus, up to 6 evidence queries); code runs them and turns each result into a fact with an id; the AI writes from those facts only, and every figure must pass the fact-id check (a failing line is dropped; too few surviving lines fall back to the facts' computed sentences). The layout is fixed: a headline, up to 3 key figures, up to 3 recommendations, up to 2 charts, then a data-quality note built by code that always shows the trust label and any data gaps, coverage warning and unreliable-figure notes. If the data has nothing on the focus, the brief says "The data has no <topic> information". Briefs are saved with the run, and can be listed, reopened, renamed, lightly edited ("Edited by you") and deleted from Lens. Without an AI a brief is built from the report's own sentences.
- **Export**: a brief, all briefs (a zip of PDFs), the report or the short report as a PDF made on the server with a headless Chrome, Chromium or Edge (`BROWSER_PATH`; the Docker image installs Chromium), or **Copy as text**. No buttons, no menus, no internal column names.
- **Every tab** (Report, Dashboard, Insights, Ideas to try, Your data, How files connect, What we cleaned, How we analysed, Power BI export, Chat) exposes its items with stable ids to the agent, so questions, focus, view edits and briefs work from any tab; a test fails if a tab exposes none.
- **Feedback**: a thumbs up or down on every answer is written to `lens_feedback.jsonl` with the plan and tools used. `python scripts/lens_feedback_report.py` summarises the most common unmet requests and the thumbs-down patterns.
- **Evaluation set**: `backend/tests/lens_eval/requests.jsonl` (136 requests in 19 categories, 26 needing custom analysis). `python scripts/lens_eval.py` replays it with a scripted fake planner (it checks every expected plan is valid and runs on the sample run); `python scripts/lens_eval.py --real` lets the real AI plan each request and reports plan accuracy by category.

Tests: `test_agent_loop.py`, `test_agent_custom.py`, `test_agent_briefs.py`, `test_lens_eval.py`.

### View edits: change what you see, by hand or by chat

Every card, chart, insight, action and report section has a small **Hide** control and a **Change** menu (rename, pin, move a section, and for a chart its type, time grain or top N), and every card and chart has **Ask Lens about this**. At the top of each tab: **Full view / Short view**, **Undo**, **Reset view**, and **Hidden items (n)** with a Restore button for each.

Chat uses the same mechanism. The AI returns a command from a closed list (hide or show an item or section; full or short view; show only the top N; show only the charts that support an action or the summary; change a chart's type, time grain or top N; move a section up or down; rename a title; pin or unpin); **code validates it** (the item or section id must exist) and applies it with the same function the buttons use, with a one-click **Undo**. Each command is small, validated and idempotent. There are no free-form styling or layout commands: asked for one, Lens shows the available changes as chips. Plain phrases ("make this tab shorter", "undo", "show only the top 3") are read by simple rules with no AI call; with no AI at all the buttons and the chips still work.

- **Short view.** Insights: each insight is one line plus its chart, the top 5 by the report's own order are shown and the rest sit under **Show more**. Report and Dashboard: at most 6 charts, and only those an action or the summary cites (code scores each chart by that).
- **Nothing is deleted and no figure changes.** The edits are saved with the run (`lens.json`, key `view_edits`), survive a reload, and never touch `result.json` or `report.md`. Hidden items appear in the print panel as optional checkboxes. A renamed title is the user's own text, labelled **Edited by you**.
- **Redrawn charts.** Changing a chart's type, grain or top N redraws it through the same checked Lens query and chart code, as a separate picture labelled as redrawn; the report's own chart and sentences are unchanged. Only group comparisons and trends can be redrawn.
- **The sample run is shared**, so its edits are kept in your browser only.
- **Ask Lens about this** opens Lens with the item as a removable chip, "Focus: <title>". Questions are answered with that item's own saved text (sent to the AI with the question; without an AI the item's text is quoted) until you remove the chip.

Code: `backend/app/viewedit.py` (items, commands, validation, undo, what each tab shows), `backend/app/lens/viewchat.py`, `viewchart.py`; interface: `frontend/src/components/view/`. Tests: `backend/tests/test_partb_view.py`.

## Quick start

```bash
# 1. backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. frontend (needs Node 18+)
cd ../frontend && npm install && npm run build         # builds into frontend/dist

# 3. run everything on one port
cd ../backend && uvicorn app.main:app --port 8000
# open http://localhost:8000 and upload sample_data/sales.csv
```

Development mode with hot reload: run `uvicorn app.main:app --reload` in `backend/` and `npm run dev` in
`frontend/`, then open http://localhost:5173.

## Pick a free brain (optional)

With no setup it runs in **rules-only** mode and still produces a complete, honest report. For smarter goal
understanding and nicer summaries, choose one:

| Option | Setup |
|---|---|
| **Ollama** (local, offline, free) | Install from ollama.com, then `ollama pull qwen2.5:7b` and `pip install langchain-ollama` |
| **Gemini** (free tier) | Key from Google AI Studio, then `pip install langchain-google-genai` and `export GOOGLE_API_KEY=...` |
| **Groq** (free tier) | Key from console.groq.com, then `pip install langchain-groq` and `export GROQ_API_KEY=...` |

The provider is auto-detected (see `.env.example` to force one or change the model; free-tier model names change over
time, so set `LLM_MODEL` if the default is retired). The "Brain" pill in the top bar shows what is active.

**Privacy:** only aggregated metadata (column names, types, summary statistics, finding text, and the business context you typed) is sent to an LLM, never
raw rows. With Ollama nothing leaves your machine.

## Power BI

Power BI Desktop is Windows-only and `.pbix` is a proprietary binary, so the app does not pretend to generate one.
The **Power BI pack** (button in the UI, or `GET /api/runs/{id}/powerbi`) contains:

- `cleaned_data.csv` / `.parquet`: load with *Get data* (with several files, also the original tables and `relationships.csv`)
- `data_dictionary.csv`: detected types and recommended Power BI types
- `measures.dax`: ready-to-paste measures (totals, rates, shares, month-over-month, YTD)
- `visuals.json` + `POWERBI_GUIDE.md`: which visual to build for each finding, step by step
- `theme.json`: matching colour theme
- `report.md` + charts

Next step if you want it: push datasets straight to the Power BI service through its REST API (needs an Azure AD app
registration and a Power BI Pro licence, so it is not part of the free path).

## Tests

```bash
cd backend && pytest -q        # 737 tests: pipeline, API, edge cases, multi-table joins, clarifying questions and glossary, derived metrics, report tiers, run safety, LLM failure modes (fake model, no network)
```

**Scorecard.** `python scripts/scorecard.py [--dataset mall] [--runs N]` runs the real pipeline on a built-in test dataset (Kaveri, 3 files; or mall, 40 000 rows) and checks the report against a known answer in 12 checks. Both datasets were built for this project. Latest recorded results: Kaveri 12/12 and mall 12/12 (25 Oct 2026). See `docs/facts.md` for measured limits, timings and storage.

## Layout

```
backend/app/   profiler.py  relations.py  brief.py  signals.py  placeholders.py  clarify.py  directives.py  glossary.py  planner.py  derived.py  derived_check.py
               measures.py  polarity.py  retail_roles.py  cleaner.py  tools.py  critic.py  insights.py  strategy.py  summary.py  tiering.py  goal.py
               wording.py  reporter.py  powerbi.py  dashboard.py  graph.py (LangGraph)  llm.py (providers)  runner.py  events.py  main.py (FastAPI)
backend/tests/ 737 tests        scripts/  scorecard.py  make_mall.py        docs/  facts.md  decision_points.md  flow_audit.md
frontend/src/  App.tsx  tokens.css (colours, type and spacing: the only values new code uses)  layout.css (the grid)  components/ (StartPage = the product page, Dialog, UploadForm, QuestionForm, RunView, Results, ReportPage, lens/ (the Lens button, panel, Chat tab and pins), PrintPanel, BIDashboard, InsightCard, StrategyView, DataViewer, DataModel, Glossary, History, ...)
sample_data/   sales.csv  churn.csv  shop/{customers,orders,products}.csv  mall/ (40 000-row scorecard dataset with truth.json)  demo/sales_run/ (the read-only sample run)
scripts/       report_check.py  scorecard.py  landing_screenshots.js  design_notes.md  build_log.md
```

## Privacy: what is stored and where

Everything stays on the machine running the server.

| What | Where | Kept for |
|---|---|---|
| Your uploaded files (a copy, as received) | `backend/data/runs/<run id>/inputs/` | `RETENTION_DAYS` (30) |
| Cleaned and joined tables, the report, charts, Power BI pack, status and timeline | `backend/data/runs/<run id>/` | `RETENTION_DAYS` (30) |
| Answers and business context you typed | the run folder, and the company's entry in `backend/data/glossary.json` (`GLOSSARY_PATH`) | glossary: until you delete it |
| Lens conversation, pins and question count | `backend/data/runs/<run id>/lens.json` | with the run |
| Saved AI replies (keyed by the full prompt, model and temperature) | `backend/data/ai_cache/` (`AI_CACHE_DIR`; `AI_CACHE=off` turns it off) | until you delete the folder |

If an LLM is configured, only aggregated metadata is sent to it: column names, types, summary statistics, findings and the business context you typed. Raw rows are never sent. Lens sends your question, the column descriptions and the date spans (no rows, no values), the tab name and the last 6 turns of the conversation; the figures in an answer are never sent. Your business context is sent as-is, so leave out anything confidential. With no LLM configured nothing leaves the machine.

To delete everything, stop the server and remove `backend/data/`. To keep runs for less time, set `RETENTION_DAYS` (for example `7`); `0` keeps them forever.

## Known limits

- The sample run is built from `sample_data/sales.csv`, not the Kaveri dataset named in the design brief: Kaveri is not in this repository.
- Printing covers the report tab only. The cleaning log and plan are separate tabs and are not part of the printed report.
- Charts are static images (PNG), not interactive: you can click to enlarge but not filter them. Power BI is the place for interactive slicing; the pack is built for that.
- Non-English column names need a system font that has those letters (Nirmala UI on Windows, Noto Sans elsewhere).

- **Daily AI token limit.** Free-tier providers cap the tokens per day as well as per minute. Request pacing (`AI_TPM_LIMIT`) keeps calls inside the per-minute window, but when the day's limit is reached the app falls back to rules for that step and says so, and Lens offers only its suggested questions until it resets. `DAILY_AI_RUNS` and `DAILY_LENS_QUESTIONS` cap use across all visitors of a public copy.
- **Data is not stored securely beyond retention.** Uploads and results sit unencrypted in the runs folder (`RETENTION_DAYS`, 30 by default) and the access code is a shared secret, not a user account. Do not upload data you would not keep on that server; delete a run's folder to remove it earlier.
- **Unusual-change thresholds are untuned.** Lens flags a change as unusual at more than 3 median absolute deviations (and at least 10%), or at least 40%. These numbers were chosen by hand and checked on test data only.
- **The premise check covers rank, comparison and direction claims only** ("why is X the top?", "X is higher than Y", "sales went down in March"). Other assumptions inside a question are not checked.
- **Custom analysis runs SQL the AI wrote.** It is parsed and restricted to one read-only SELECT, but it is new and untested with a real model; the cross-check against a closed operation only happens when the plan supplies one, and a result that is not aggregated is shown, not verified.
- **Local use only.** There is no authentication (an optional shared access code can gate uploads, see *Deploy*) or user separation, and run folders live on disk (`backend/data/runs`). Anyone who can reach the server can list recent runs and read any run whose id they have. Runs older than `RETENTION_DAYS` (30 by default) are deleted when the server starts and whenever a new run is created; a run still queued or running is never deleted. Run folders are 8 to 20 times the size of the input while they exist.
- **No run queue.** Each upload starts its own thread immediately; there is no cap, parallel runs were not tested, and a server restart loses runs in flight (they stay `running` or `queued`).
- **Size.** Each table is limited to `MAX_ROWS` rows (500 000 by default). A larger file is refused at upload with a message that gives its row count and the limit. There is no column limit, and only 8 files and 50 MB in total. The largest dataset tested is 40 000 rows by 13 columns, which took about 1.5 to 2.5 minutes on the Groq free tier; most of that time is LLM calls and chart rendering, not row processing. Much larger files are untested and the whole upload is held in memory.
- **Formats.** `.xls` is accepted by the upload check but needs `xlrd`, which is not in `requirements.txt`; save as `.xlsx` instead. `.json` must be table-shaped.
- **Free-tier LLMs** rate-limit and sometimes time out (60 s timeout, one retry). The app then falls back to rules for that step and says so in the report; runs get slower and less tailored but still finish.
- **Retail-centred helpers.** Detecting quantity times price as an amount (`retail_roles.py`) and the test datasets are retail. Detection uses values, not column names, and the module is swappable, but other domains get only the generic path.
- Joins use single-column keys (no composite keys) and never join many-to-many. A table that can only be reached through a summarised table (e.g. `products` when the grain is one row per customer) is reported as not used; ask a question at that level instead. Up to 8 files and 50 MB in total by default (`MAX_FILES`, `MAX_UPLOAD_MB`).
- Key detection is heuristic. It is conservative, and always shows the match rate, but check the *Data model* tab before trusting a surprising result.
- Associations, not causes. The report says so, and the Critic flags weak or small effects rather than recommending on them.
- Ambiguous `dd/mm` vs `mm/dd` dates: the order is inferred from the data; if every value is ambiguous, day-first is assumed and the cleaning log says so.
- Small local models (7B) plan less reliably than Gemini/Groq. That is why every plan is validated.
