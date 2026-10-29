"""Report readability (growth sections), label merge, date sanity and multi-sheet workbooks. Generic data only: no column or domain words matter."""
import io
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import clarify, insights, reporter, summary, tiering  # noqa: E402
from app.cleaner import apply_directives, coerce_types  # noqa: E402
from app.profiler import profile_df  # noqa: E402

from test_multitable import csv_bytes, run_files  # noqa: E402
from test_report_tiers import make_state  # noqa: E402


# ---------------------------------------------------------------- 1. growth sections

def growth(group, n_groups, focus):
    rows = [{"group": f"g{i}", "first": 100.0, "last": 100.0 + i, "change": float(i), "pct": None, "line": f"g{i} +{i} (from 100 to {100 + i}), not statistically reliable"} for i in range(n_groups)]
    return {"id": f"r_{group}", "tool": "ranking", "kind": "growth", "basis": "total", "group": group, "measure": "revenue", "periods": ["2023", "2024"], "groups": rows, "focus": focus, "text": "t"}


def test_growth_main_report_only_for_goal_named_groups_and_at_most_eight_lines():
    state = make_state()
    state["summary_facts"]["rankings"] += [growth("named", 12, True), growth("other", 3, False)]
    view = tiering.build_view(state)
    md = reporter.build_report(state, "Summary.", ["rec"], "rules", view)
    main, appendix = md.split("## Appendix")
    sec = main.split("## Growth in revenue by named")[1].split("\n## ")[0]
    assert sec.count("\n- g") == 8 and "- and 4 more in the appendix" in sec
    assert sec.index("g11 +11") < sec.index("g4 +4") and "g3 +3" not in sec                    # largest absolute change first
    assert "by other" not in main and "Growth in revenue by other" in appendix                 # not named by the goal: appendix only
    assert "remaining groups" in appendix and "g3 +3" in appendix


def test_growth_line_without_percent_is_one_short_form():
    rng = np.random.default_rng(1)
    d = pd.date_range("2022-01-01", "2023-12-31", freq="D")
    df = pd.DataFrame({"when": np.tile(d, 3), "seg": np.repeat(["a", "b", "c"], len(d)), "amt": rng.uniform(5, 15, 3 * len(d))})
    df.loc[(df.seg == "c") & (df.when.dt.year == 2022), "amt"] = 0.0                            # the start is zero
    state = {"goal": "amt by seg", "profile": profile_df(df), "plan": {"date_col": "when", "target": "amt"}}
    fact = summary._growth_fact(df, state, "seg", "amt", "when", 1)
    line = next(g["line"] for g in fact["groups"] if g["group"] == "c")
    assert "near zero" not in line and "no %" not in line and "from 0" in line and "%" not in line.split("(")[0]


# ---------------------------------------------------------------- 2. rare labels that abbreviate a common one

def label_df(n=1000):
    rng = np.random.default_rng(3)
    sex = np.array(["Men"] * 600 + ["Women"] * 390 + ["M"] * 6 + ["Q"] * 4)
    return pd.DataFrame({"sex": sex, "amt": rng.normal(50, 5, n)})


def build(df, goal="Explore this data"):
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    return clarify.build_questions(typed, prof, goal, {}, True, None, None), typed


def test_rare_prefix_label_triggers_a_merge_question_defaulting_to_merge():
    qs, typed = build(label_df())
    q = next(x for x in qs if x["kind"] == "label_merge")
    assert q["default"] == "merge" and q["options"][0]["action"]["mapping"] == {"M": "Men"}
    out, log, eff, _ = apply_directives(typed, qs, {})
    assert "M" not in set(out["sex"]) and (out["sex"] == "Men").sum() == 606 and eff[q["id"]]["rows"] == 6
    kept, _ = apply_directives(typed, qs, {q["id"]: "separate"})[:2]
    assert "M" in set(kept["sex"])


def test_merge_ranks_above_rare_labels_and_counts_toward_the_cap():
    assert clarify.IMPACT.index("label_merge") < clarify.IMPACT.index("rare_labels")
    qs = [{"id": k, "kind": k, "text": k, "strong": True, "options": [{"value": "d", "label": "d", "action": {"type": "note_only"}}], "default": "d"}
          for k in ("rare_labels", "near_sum", "negatives", "label_merge")]
    kept, dropped = clarify.cap_questions(qs)
    assert len([q for q in kept if not q.get("auto")]) == clarify.MAX_QUESTIONS == 3
    assert "label_merge" in [q["kind"] for q in kept] and [d["id"] for d in dropped] == ["rare_labels"]


def test_a_rare_label_that_is_not_a_prefix_does_not_ask_for_a_merge():
    df = label_df()
    df["sex"] = df["sex"].replace({"M": "Z"})
    assert not any(q["kind"] == "label_merge" for q in build(df)[0])


def test_a_code_like_column_with_many_distinct_values_does_not_ask_for_a_merge():
    rng = np.random.default_rng(5)
    codes = [f"C{i}" for i in range(80)]
    df = pd.DataFrame({"code": rng.choice(codes, 400), "amt": rng.normal(50, 5, 400)})
    assert not any(q["kind"] == "label_merge" for q in build(df)[0])


# ---------------------------------------------------------------- 3. date sanity

def date_df(days):
    months = pd.period_range("2022-01", "2023-12", freq="M")
    rows = [(p.to_timestamp() + pd.Timedelta(days=d - 1)) for p in months for d in days for _ in range(5)]
    return pd.DataFrame({"when": rows, "amt": np.arange(len(rows), dtype=float)})


def test_few_days_across_many_months_asks_to_swap_with_no_swap_default_and_a_warning():
    qs, typed = build(date_df([1, 2, 3]))
    q = next(x for x in qs if x["kind"] == "date_swap")
    assert q["text"] == "Dates look like day and month may be swapped. Swap them?" and q["default"] == "keep"
    assert q["options"][0]["action"]["type"] == "note_only"
    state = {"context": {}, "plan": {"date_col": "when", "target": "amt", "relevant_columns": ["when", "amt"]}, "profile": profile_df(typed), "questions": qs, "answers": {}}
    assert any(g["title"] == "Dates may have day and month swapped" and g["severity"] == "warn" for g in insights.data_gaps(state))
    state["answers"] = {q["id"]: "swap"}
    assert not any("swapped" in g["title"] for g in insights.data_gaps(state))
    out = apply_directives(typed, qs, {q["id"]: "swap"})[0]
    assert out["when"].dt.day.nunique() > 3 or out["when"].dt.month.nunique() <= 3         # swapped: days 1-3 became months 1-3


def test_enough_distinct_days_or_months_asks_nothing():
    assert not any(q["kind"] == "date_swap" for q in build(date_df([1, 8, 15, 22, 28]))[0])
    short = date_df([1, 2, 3]).iloc[:15]                                                       # under 6 months
    assert not any(q["kind"] == "date_swap" for q in build(short)[0])


def test_every_date_on_the_same_day_asks_no_swap_question():
    assert not any(q["kind"] == "date_swap" for q in build(date_df([1]))[0])


# ---------------------------------------------------------------- 4. multi-sheet workbooks

def test_workbook_uses_the_largest_sheet_and_lists_the_others_as_ignored():
    rng = np.random.default_rng(5)
    big = pd.DataFrame({"sku": [f"S{i}" for i in range(120)], "stock": rng.integers(0, 90, 120), "kind": rng.choice(["x", "y", "z"], 120)})
    small = pd.DataFrame({"employee": [f"E{i}" for i in range(30)], "salary": rng.normal(50, 9, 30)})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        small.to_excel(w, sheet_name="Staff", index=False)
        big.to_excel(w, sheet_name="Stock", index=False)
    rid, res = run_files({"book.xlsx": buf.getvalue()}, "What drives stock?")
    dm = res["data_model"]
    assert dm["base_table"] == "book_Stock" and [t["name"] for t in dm["ignored_tables"]] == ["book_Staff"]
    low = res["report_md"].lower()
    assert "ignored (" in low and "book_staff" in low and "no reliable key link" not in low
