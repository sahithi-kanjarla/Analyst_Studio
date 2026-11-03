"""Fix A: the order key comes from the understanding record (or, with no AI answer, from a column's name and then a question); the data only verifies it. Fake AI only."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import pandas as pd  # noqa: E402

from app import cleaner, clarify, grain, mainview, profiler, signals  # noqa: E402
from tests.fixtures_bakery import bakery, handicrafts, record  # noqa: E402


def prof_with(df, rec=None, answered=None):
    prof = profiler.profile_df(df)
    d = grain.decide(df, {c["name"]: c["role"] for c in prof["columns"]}, rec, answered)
    prof["grain_decision"] = d
    if grain.grain_of(d):
        prof["grain"] = grain.grain_of(d)
    return prof, d


def test_the_record_names_the_order_id_so_the_bakery_has_900_orders_and_nothing_is_blanked():
    df = bakery()
    prof, d = prof_with(df, record(df, "order_id"))
    assert d["verdict"] == "order_grain" and d["key"] == "order_id" and d["source"] == "ai" and d["orders"] == 900 and d["per_order"] == 1.0
    typed, log = cleaner.coerce_types(df, prof)
    assert typed["unit_cost"].notna().all() and typed["amount"].sum() == df["amount"].sum() and not any(r["step"] == "Counted order amounts once" for r in log)
    tiles = dict(mainview.tiles({"goal": "", "plan": {}, "key_metrics": [{"label": "Total amount", "value": "1"}], "profile": {**prof, "rows": 900, "cols": 13}}, {}))
    assert tiles["Orders"] == "900"


def test_a_long_format_file_still_counts_each_orders_total_once_and_keeps_a_products_price():
    df = handicrafts()
    prof, d = prof_with(df, record(df, "order_id"))
    assert d["verdict"] == "verified" and d["orders"] == 120 and "order_total" in d["amount_columns"] and "unit_price" not in d["amount_columns"]
    out, log = cleaner.coerce_types(df, prof)
    per_order = df.drop_duplicates("order_id")["order_total"].sum()
    assert out["order_total"].sum() == per_order and df["order_total"].sum() > per_order
    assert out["unit_price"].notna().all() and out["units"].sum() == df["units"].sum()
    assert any(r["step"] == "Counted order amounts once" for r in log)


def test_a_products_price_repeated_per_product_is_never_blanked_even_when_named_as_the_key():
    df = bakery()
    roles = {"order_date": "date"}
    d = grain.decide(df.assign(order_date=pd.to_datetime(df["order_date"])), roles, record(df, "item_code"))
    assert d["verdict"] == "ask" and "disagrees" in d["found"]
    assert grain.count_order_amounts_once(df.assign(order_date=pd.to_datetime(df["order_date"])), [], roles, "item_code")["unit_cost"].notna().all()
    table = pd.DataFrame({"product": [f"p{i % 10}" for i in range(200)], "price": [5.0 + (i % 10) for i in range(200)], "day": pd.date_range("2025-01-01", periods=200).astype(str)})
    assert grain.count_order_amounts_once(table, [], {}, "product")["price"].notna().all()


def test_with_no_ai_unique_candidate_does_not_ask():
    """When the candidate order key is unique per row, the file is already at order grain — do not ask."""
    df = bakery()
    prof, d = prof_with(df, None)
    assert d["verdict"] == "order_grain" and d["key"] == "order_id" and d["source"] == "name"
    assert d["orders"] == 900 and d["per_order"] == 1.0
    typed, log = cleaner.coerce_types(df, prof)
    assert typed["unit_cost"].notna().all() and not any(r["step"] == "Counted order amounts once" for r in log)
    sigs = [s for s in signals.compute_signals(typed, prof) if s["type"] == "order_key"]
    assert len(sigs) == 0


def test_with_no_ai_non_unique_candidate_asks():
    """When the candidate has repeated values and could be deduplicated, ask."""
    df = handicrafts()
    prof, d = prof_with(df, None)
    assert d["verdict"] == "ask" and "order_id" in d["candidates"]
    typed, log = cleaner.coerce_types(df, prof)
    sigs = [s for s in signals.compute_signals(typed, prof) if s["type"] == "order_key"]
    assert len(sigs) == 1
    qs = clarify.template_questions(sigs, prof, "", {}, True)
    assert qs[0]["text"] == "Which column identifies one order?"


def test_an_answer_names_the_key_and_none_leaves_no_grain():
    df = handicrafts()
    prof, d = prof_with(df, None, "order_id")
    assert d["verdict"] == "verified" and d["source"] == "answer"
    prof2, d2 = prof_with(df, None, "none")
    assert d2["verdict"] == "none" and "grain" not in prof2


def test_a_record_that_names_a_column_the_data_contradicts_keeps_every_value_and_asks():
    df = handicrafts()
    d = grain.decide(df, {}, record(df, "region"))          # three values: too few to be one per order
    assert d["verdict"] == "ask" and d["key"] == "region" and "region" in d["candidates"] and "order_id" in d["candidates"]
    assert grain.grain_of(d) is None
    d2 = grain.decide(df, {}, record(df, "nonexistent_column"))          # an invalid name is no answer
    assert d2["verdict"] == "ask" and d2["source"] == "name"
    assert grain.decide(df, {}, record(df, "none"))["verdict"] == "none"


def test_the_pipeline_on_the_bakery_without_ai_does_not_ask_because_order_id_is_unique(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("GLOSSARY_PATH", str(tmp_path / "g.json"))
    from app import runner
    df = bakery()
    rid = runner.create_run("", [("bakery.csv", df.to_csv(index=False).encode())])
    runner._execute(rid)
    wd = runner.run_dir(rid)
    st = runner.read_status(rid) or {}
    if st.get("status") == "needs_input":
        qs = json.loads((wd / "questions.json").read_text(encoding="utf-8"))["questions"]
        assert not any(q["text"] == "Which column identifies one order?" for q in qs)
        (wd / "answers.json").write_text(json.dumps({"answers": {}, "skip": True, "notes": ""}))
        runner._execute(rid)
    clean = pd.read_pickle(wd / "clean.pkl")
    assert clean["unit_cost"].notna().sum() == 900
