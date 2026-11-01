# Evaluation: what is checked, and what the saved results say

Every number below is copied from a result file saved in this repository, and each one says which file. Nothing here was measured again for this page, and no figure is estimated. Where a check has no saved output, that is said.

## 1. Scorecards on known answers

`python scripts/scorecard.py [--dataset mall] [--runs N]` runs the real pipeline on a built-in test dataset and checks the finished report against a truth that is known because the data was generated for the test (`scripts/make_mall.py`, `sample_data/mall/truth.json`). It needs a model for the full path; the saved runs below used Groq `openai/gpt-oss-120b`.

**Kaveri (3 files, 12 checks)**: revenue total, profit total and margin, the top tier and its share, tier growth, a margin difference that is *not* reliable and must be said so, a loss-making category, discount against margin, seasonality, at most three questions with none out of scope, a placeholder value handled, an id-like column not used as a measure, and ratios computed as sum over sum.

| Saved file | Result |
|---|---|
| `scripts/scorecard_kaveri_out.txt` | 1 run, **12 of 12** checks passed. Revenue truth 45,422,281, found 45,422,281. |
| `scripts/stage2_kaveri_run_out.txt` | 1 run, **12 of 12**. |
| `scorecard_output.txt` (repository root) | 3 runs, per run 12, 12, 12; median 12/12, minimum 12/12. |

**Mall (one 40,000-row file, 12 checks)**: revenue within 1.5% (blank quantities are left out, so the figure is expected to differ slightly), top store, a shrinking store, a high-return brand, a festive peak in October and November with a weak June, a dominant department (about 49%), decoy columns not named as drivers, no invented margin or profit, the data-gap line saying margin cannot be computed, an id-like column not used as a measure, at most three relevant questions, and five deliberate mess items handled.

| Saved file | Result |
|---|---|
| `scripts/scorecard_mall_out.txt` | 2 finished runs, **12 of 12** each; median 12.0/12, minimum 12/12. Revenue truth 81,887,060, found 81,105,167 (inside the 1.5% allowance). |

Limits of this evidence: the datasets are synthetic and were written by the same people who wrote the checks; Kaveri passes were recorded with the AI's saved replies reused (`scripts/scorecard_kaveri_out.txt` lists the AI stages answered from saved results and reports 0 requests sent in that run). The scorecards test the report's content, not its layout.

## 2. The report-check script

`python scripts/report_check.py <run id or folder>` reads a finished run and prints PASS or FAIL for seven plain-text rules. It never calls an AI and changes nothing. Exit code 0 only when every rule passes.

1. The recommendation lists are identical wherever they appear (What to do, Top recommendations, the saved actions).
2. No action is a bare fact: each one starts with an instruction.
3. A "biggest / highest / top" claim names the group the computed ranking says is on top.
4. No investment advice from size alone: an action that says to invest, spend or scale up must cite an effect or a reliable difference.
5. The coverage warning ("days in this period have data") appears exactly when under 70% of calendar days have data.
6. No internal column names (`discount_pct`) in the main view.
7. No hash links in the report.

Saved output: none is stored as a file. Re-running it on the shipped sample (`python scripts/report_check.py sample_data/demo/sales_run`) printed "7 of 7 rules pass" when this page was written, and `backend/tests/test_report_check.py` holds one good and one bad sample for each rule.

## 3. The fact-id citation check

Where an AI writes prose (the summary, strategy wording, Lens summaries), `backend/app/cited.py` requires a fact id in square brackets after every figure and checks, per figure: the id exists; the figure equals a figure of that fact (same rounding rules as the report's number check); the words around it (6 words either side) name the fact's subject; and a percent change from a growth or trend fact also names that fact's measure and period (9 words either side). A sentence that fails any check is dropped, the rule-based sentence is kept, and the reason is recorded. This is verified by unit tests with good and bad examples (`backend/tests/test_report_consistency.py`, `test_summary_answers.py`, `test_lens_stage2a.py`); no real-AI figure for how often a model's sentence is dropped is saved in the repository.

## 4. Lens against pandas

`scripts/lens_kaveri_check.py` and `scripts/lens_fixpack_check.py` ask the real model questions about the Kaveri run (6,400 rows, per the saved output) and compare each answer with what pandas computes directly on the cleaned data, outside Lens.

| Saved file | Result |
|---|---|
| `scripts/lens_kaveri_check_out.txt` | **8 of 8** answers match pandas (includes two follow-ups and two chart-control requests). One AI call per question. |
| `scripts/lens_fixpack_check_out_run1.txt` | **9 of 9** answers match pandas or the report's own saved records. |
| `scripts/lens_stage2a_check.py` | The README says its output is saved as `scripts/lens_stage2a_check_out.txt`; that file is not in the repository, so no result is quoted here. |

The first real-model run of the 8-question check scored 7 of 8: the model labelled its reply with an operation name instead of "query", and Lens refused it. That is recorded in `scripts/build_log_v3.md`; Lens now reads the reply by what it carries.

## 5. The test suite

The tests use fake models (no network, no key). Saved full-suite results in the repository, from different points in the project's history (the suite grew over time):

| Saved file | Result |
|---|---|
| `backend/full_suite.txt` | 419 passed, 361.63 s |
| `backend/full_suite_understanding.txt` | 490 passed, 298.30 s |
| `scripts/stage2_full_suite_out.txt` | 631 passed, 370.09 s |
| `scripts/lens_full_suite_out.txt` | 737 passed, 430.77 s |
| `backend/full_suite_polish.txt` | 654 passed, 1 failed (`test_failed_and_unusable_calls_are_not_cached`), 3306.96 s |

The Part B suite result is in `scripts/build_log_partb.md`.

## What is not evaluated

- No saved result measures the quality of the report's wording, or how often an AI-written sentence is rejected.
- There is no evaluation on real sellers' files beyond the test datasets above.
- The scorecards and Lens checks ran on a single model (Groq `openai/gpt-oss-120b`); no other model was scored.
