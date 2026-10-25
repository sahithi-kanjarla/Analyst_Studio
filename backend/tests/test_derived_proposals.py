"""The brief proposes derived calculations and the code checks them. Fake model, no network, neutral column names on purpose.

(a) quantity x a code on a table that has a line total: rejected, the totals stay the line-total column.
(b) quantity x unit price on a table with a real unit price: accepted and built.
(c) a garbage reply: the value rule runs, and it never picks an id-like column."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app import brief as briefmod, derived_check, graph, retail_roles as rr  # noqa: E402
from app.cleaner import coerce_types  # noqa: E402
from app.config import RUNS_DIR  # noqa: E402
from app.context import normalise_context  # noqa: E402
from app.profiler import profile_df  # noqa: E402
from test_clarify import cl, result, start, wait  # noqa: E402,F401
from test_propose import Seq  # noqa: E402

GOAL = "Which shop earns the most?"


def table(n=600, seed=11, line_total=True):
    rng = np.random.default_rng(seed)
    pcs = rng.choice([1, 2, 3, 4], n, p=[0.5, 0.25, 0.15, 0.1]).astype(float)
    each = np.round(rng.lognormal(6.0, 0.4, n), 2)
    df = pd.DataFrame({"shop": rng.choice(["North", "South", "East", "West"], n), "pcs": pcs, "each": each,
                       "zip": rng.choice(np.arange(100000, 999999), n, replace=False),
                       "when": (pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 300, n), unit="D")).strftime("%Y-%m-%d")})
    if line_total:
        df["line_amt"] = np.round(pcs * each, 2)
        df = df.drop(columns="each")
    return df


def reply(derived, already_total=None, columns=None):
    base_cols = {"pcs": {"role": "measure", "kind": "additive"}, "zip": {"role": "measure"}, "shop": {"role": "dimension"}}
    return json.dumps({"columns": columns or base_cols, "kpis": {}, "derived_measures": derived, "already_total": already_total, "why": "test"})


def prop(name, left, right, op="mul", confidence="high"):
    return {"name": name, "op": op, "left": left, "right": right, "meaning": "m", "confidence": confidence, "reason": "r"}


class Brain:
    """Answers only the analysis brief; every other prompt gets non-JSON, so the other stages use their rules."""
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        text = self.content if "reading the column list" in messages[0][1] else "not json"
        return type("R", (), {"content": text})()


@pytest.fixture
def brain(monkeypatch):
    def use(content):
        monkeypatch.setattr(graph, "get_llm", lambda: (Brain(content), "fake"))
    return use


def judge_for(df, derived, already_total=None, columns=None, kpis=()):
    st = {}
    b = briefmod.resolve(Seq(reply(derived, already_total, columns)), df, profile_df(df), GOAL, normalise_context({"kpis": list(kpis)}), st)
    return b, st["decisions"]


# ---- (a)

@pytest.mark.parametrize("right, why", [("zip", "id or a code"), ("line_amt", "already holds the line total")])
def test_a_quantity_times_a_code_or_the_line_total_is_rejected(right, why):
    b, decisions = judge_for(table(), [prop("revenue", "pcs", right)], already_total="line_amt")
    assert b["derived"]["accepted"] == [] and len(b["derived"]["rejected"]) == 1
    assert "proposed by AI, rejected by code" in b["derived"]["rejected"][0]["verdict"] and why in b["derived"]["rejected"][0]["verdict"]
    assert any("rejected by code" in d["why"] for d in decisions)


def test_the_twenty_times_check_catches_a_code_the_values_alone_did_not():
    df = table()
    df["zip"] = df["zip"].astype(float) + 0.5      # not whole numbers, declared additive: only the size check can tell
    b, _ = judge_for(df, [prop("revenue", "pcs", "zip")], already_total="line_amt", columns={"pcs": {"role": "measure", "kind": "additive"}, "zip": {"role": "measure", "kind": "additive"}})
    assert b["derived"]["accepted"] == [] and "more than 20x" in b["derived"]["rejected"][0]["verdict"]


def test_the_run_keeps_the_line_total_as_the_amount(brain):
    df = table()
    brain(reply([prop("revenue", "pcs", "zip")], already_total="line_amt"))
    rid = start(df, goal=GOAL)
    status = wait(rid)
    if status == "needs_input":
        assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True}).status_code == 200
        assert wait(rid) == "done"
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    assert "revenue" not in clean.columns
    res = result(rid)
    assert res["plan"]["target"] == "line_amt"
    assert clean["line_amt"].sum() == pytest.approx(df["line_amt"].sum())
    assert not any(q["kind"] == "product_basis" for q in res.get("questions") or [])
    assert any("rejected by code" in d["why"] for d in res["decisions"])


# ---- (b)

def test_quantity_times_a_real_unit_price_is_accepted_and_built(brain):
    df = table(line_total=False)
    columns = {"pcs": {"role": "measure", "kind": "additive"}, "each": {"role": "measure", "kind": "rate"}, "zip": {"role": "id"}, "shop": {"role": "dimension"}}
    brain(reply([prop("line_value", "pcs", "each")], columns=columns))
    rid = start(df, goal=GOAL)
    status = wait(rid)
    if status == "needs_input":
        assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True}).status_code == 200
        assert wait(rid) == "done"
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    assert "line_value" in clean.columns and np.allclose(clean["line_value"], df["pcs"] * df["each"])
    res = result(rid)
    assert res["plan"]["target"] == "line_value"
    assert any("proposed by AI, accepted by code" in d["why"] for d in res["decisions"])


def test_low_confidence_or_unsettled_data_asks_with_no_derivation_as_the_default():
    df = table(line_total=False)
    columns = {"pcs": {"role": "measure", "kind": "additive"}, "each": {"role": "measure", "kind": "rate"}, "zip": {"role": "id"}}
    b, _ = judge_for(df, [prop("line_value", "pcs", "each", confidence="low")], columns=columns)
    typed, prof = coerce_types(df, profile_df(df))[0], profile_df(df)
    pm = rr.derive_product_measure(typed, {c["name"]: c for c in prof["columns"]}, briefmod.kinds(b), b)
    assert pm["basis"] == "unclear" and pm["default"] == "total"
    sig = {"id": "s", "evidence": {**pm, "count": pm["count"], "rate": pm["rate"]}}
    q = rr.product_question(sig, set(df.columns))
    assert q["default"] == "total" and not q.get("auto")


def test_blank_quantity_question_only_when_a_derived_column_is_used():
    from app import signals
    df = table(line_total=False)
    df.loc[:9, "pcs"] = np.nan
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    cols = {c["name"]: c for c in prof["columns"]}
    unit = {"derived": {"answered": True, "accepted": [{**prop("v", "pcs", "each"), "verdict": ""}], "rejected": []}}
    assert [s["type"] for s in signals.blank_values(typed, cols, None, unit)] == ["blank_values"]
    low = {"derived": {"answered": True, "accepted": [{**prop("v", "pcs", "each", confidence="low"), "verdict": ""}], "rejected": []}}
    assert signals.blank_values(typed, cols, None, low) == []
    none = {"derived": {"answered": True, "accepted": [], "rejected": []}}
    assert signals.blank_values(typed, cols, None, none) == []


# ---- (c)

def test_garbage_falls_back_to_the_rules_and_never_picks_an_id_like_column():
    df = table(line_total=False)
    b = briefmod.resolve(Seq("this is not json"), df, profile_df(df), GOAL, normalise_context({}), {})
    assert b["source"] == "fallback" and b["derived"] is None
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    pm = rr.derive_product_measure(typed, {c["name"]: c for c in prof["columns"]}, {}, b)
    assert pm and pm["rate"] == "each" and pm["count"] == "pcs"
    no_price = typed.drop(columns="each")
    assert rr.derive_product_measure(no_price, {c["name"]: c for c in profile_df(no_price)["columns"]}, {}, b) is None      # only the code column is left: nothing is derived


def test_the_rule_skips_a_quantity_that_is_almost_always_one():
    df = table(line_total=False)
    df["pcs"] = np.where(np.arange(len(df)) % 40 == 0, 2.0, 1.0)
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    assert rr.derive_product_measure(typed, {c["name"]: c for c in prof["columns"]}) is None


def test_a_quantity_is_never_offered_as_rare_labels():
    from app import signals
    rng = np.random.default_rng(2)
    n = 400
    df = pd.DataFrame({"pcs": rng.choice([1.0, 2.0, 3.0, 4.0, 9.0, 12.0], n, p=[0.9, 0.06, 0.03, 0.005, 0.0025, 0.0025])})
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    cols = {c["name"]: dict(c, role="categorical") for c in prof["columns"]}
    assert signals.rare_labels(typed, cols) == []


def test_parse_drops_malformed_proposals():
    out, problems = derived_check.parse([{"op": "pow", "left": "a", "right": "b"}, {"op": "mul", "left": "a", "right": "b", "confidence": "sure"}])
    assert len(out) == 1 and out[0]["confidence"] == "low" and len(problems) == 1
