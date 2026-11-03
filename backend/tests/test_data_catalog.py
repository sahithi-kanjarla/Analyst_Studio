"""The data card and data_catalog: what is IN the data, answered from what the table holds. Fake AI; every concept, value and column here belongs to this test file only."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app.lens import chat, store
from app.lens.agent import datacard, loop, make_env
from app.lens.agent.tools.data_catalog import DataCatalog, closest_columns, column_matches, value_matches
from tests.test_lens import FakeAI, make_run

N = 120


def build_df():
    rng = np.random.default_rng(5)
    state = np.array(["delivered"] * 90 + ["returned"] * 18 + ["cancelled"] * 12)
    rng.shuffle(state)
    refund = np.array([None] * N, dtype=object)
    refund[:12] = ["damaged", "late", "damaged", "wrong item"] * 3
    people = np.array(["Priya Sharma", "Rahul Verma", "Anita Rao", "Dev Malhotra", "Sana Qureshi"] * 24)
    return pd.DataFrame({
        "order_date": pd.to_datetime("2024-01-01") + pd.to_timedelta(np.arange(N) % 60, unit="D"),
        "city": ["Delhi", "Mumbai", "Pune"] * 40, "amount": (np.arange(N) % 7 + 1) * 10.0, "state": state, "refund_reason": refund,
        "pack_kind": ["Box", "Envelope", "Bag", "Box"] * 30, "buyer": people, "contact": [f"user{i}@example.com" for i in range(N)], "reach": [f"+91 98{i:08d}" for i in range(N)],
        "ref": [f"ORD-{1000 + i}" for i in range(N)]})


def col(role, meaning, display, **kw):
    return {"role": role, "meaning": meaning, "display_name": display, **kw}


UND = {"source": "llm", "goal_columns": {}, "descriptive": [], "unmapped": [], "retries": 0, "error": "", "columns": {
    "order_date": col("date", "when the order was placed", "Order date"), "city": col("category", "city of delivery", "City"), "amount": col("measure", "order value", "Amount", measure_kind="money"),
    "state": col("category", "what happened to the order", "Order state"), "refund_reason": col("category", "why money was paid back", "Refund reason"),
    "pack_kind": col("category", "the kind of parcel or package used", "Pack kind"), "buyer": col("category", "who bought", "Buyer"), "contact": col("category", "how to reach the buyer", "Contact"),
    "reach": col("category", "number to call", "Reach"), "ref": col("id", "order reference", "Reference")}}


@pytest.fixture
def cat_run(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    rid, wd, res = make_run(build_df(), UND, {"goal": "How are orders doing?"})
    df, ctx = chat.load_data(wd, res)
    return rid, wd, res, df, ctx


def plan(lookup="find", **args):
    return {"plan": [{"tool": "data_catalog", "args": {"lookup": lookup, **args}}]}


def ask(run, ai, text, tab="Report"):
    rid, wd, res, df, ctx = run
    return chat.ask(wd, res, question=text, llm=ai, allow_ai=ai is not None, tab=tab)["answer"]


# ------------------------------------------------------------------ the card
def test_the_card_has_no_rows_and_withholds_personal_columns(cat_run):
    rid, wd, res, df, ctx = cat_run
    card = datacard.get(ctx, df)
    text, left = datacard.render(card, "", "")
    assert left == 0 and "returned (" in text and "delivered (" in text and "from 2024-01-01" in text and "% missing" in text
    for secret in list(df["buyer"].unique()) + ["user7@example.com", "example.com", "+91 98", "ORD-1003", "98000"]:
        assert secret not in text, secret
    byname = {d["name"]: d for d in card["columns"]}
    for c in ("buyer", "contact", "reach", "ref"):
        assert byname[c]["values"] == [] and byname[c]["withheld"], c
        assert str(byname[c]["distinct"]) and "values not listed" in [l for l in text.splitlines() if l.startswith(f"[{byname[c]['id']}]")][0]
    assert byname["state"]["values"][0][0] == "delivered" and byname["state"]["values"][0][1] == 90 and len(byname["pack_kind"]["values"]) == 3
    assert byname["refund_reason"]["missing"] == 0.9
    assert card["columns"][0]["id"] == "d1" and ctx.memo["datacard"] is card                                   # built once, kept with the run


def test_personal_looking_columns_never_leak_values_even_when_labelled_as_categories():
    n = 200
    df = pd.DataFrame({"a": [f"{f} {l}" for f, l in zip(["Asha", "Ravi", "Meera", "Karan"] * 50, ["Iyer", "Nair", "Das", "Bose", "Roy"] * 40)], "b": [f"p{i}@site.org" for i in range(n)],
                       "c": [f"0{9000000000 + i}" for i in range(n)], "d": ["12 Long Street, Springfield " + str(i) for i in range(n)], "e": ["AB%05d" % i for i in range(n)], "f": ["north", "south"] * 100})
    from app.lens.context import Col, Ctx
    ctx = Ctx({k: Col(k, k, "category") for k in df.columns}, n, None)
    card = datacard.build(df, ctx)
    text, _ = datacard.render(card)
    for v in ("Asha", "Iyer", "@site.org", "9000000", "Long Street", "AB000"):
        assert v not in text, v
    assert "north" in text and {d["name"]: bool(d["withheld"]) for d in card["columns"]} == {"a": True, "b": True, "c": True, "d": True, "e": True, "f": False}


def test_the_card_respects_the_token_cap_and_keeps_the_columns_that_matter_to_the_request():
    n = 50
    cols = {f"col_{i:03d}": ["x", "y", "z"] * 16 + ["x", "y"] for i in range(300)}
    cols["warranty_period"] = ["short", "long"] * 25
    df = pd.DataFrame(cols)
    from app.lens.context import Col, Ctx
    ctx = Ctx({k: Col(k, k.replace("_", " ").capitalize(), "category", "something recorded") for k in df.columns}, n, None)
    card = datacard.build(df, ctx)
    text, left = datacard.render(card, "what is the warranty period", "")
    assert len(text) <= datacard.MAX_TOKENS * datacard.CHARS_PER_TOKEN + 600 and left > 100
    assert "warranty_period" in text and f"{left} more columns are not shown" in text
    plain, left2 = datacard.render(card, "something else", "")
    assert "warranty_period" not in plain.split("more columns are not shown")[0] or left2 > 0


# ------------------------------------------------------------------ the catalog
def hits(run, **args):
    rid, wd, res, df, ctx = run
    env = make_env(wd, res, df, ctx, "x", "", None, False, None, None)
    return DataCatalog().run({"lookup": "find", **args}, env, [])


def test_it_finds_a_concept_by_value_by_column_name_and_by_meaning_and_every_figure_is_the_computed_count(cat_run):
    rid, wd, res, df, ctx = cat_run
    r = hits(cat_run, terms=["returned"])
    a = r.answer
    n = int((df["state"] == "returned").sum())
    assert a["text"] == f"Yes: column “Order state” contains “returned” in {n:,} rows ({n / len(df) * 100:.1f}%)." and a["figures"][0]["value"] == n == 18
    assert a["table"]["rows"][0][2] == f"{n:,}" and a["fact_ids"] and a["label"] == "About your data"
    by_name = hits(cat_run, terms=["amount"]).answer["text"]
    assert by_name.startswith("Yes: the data has a column “Amount”") and "0.0% of its values are missing" in by_name
    by_meaning = hits(cat_run, terms=["parcel"]).answer["text"]
    assert "“Pack kind”" in by_meaning and "the kind of parcel or package used" in by_meaning
    both = hits(cat_run, terms=["cancelled", "returned"]).answer
    assert sorted(f["value"] for f in both["figures"]) == [12, 18]


def test_a_clear_no_names_the_closest_columns(cat_run):
    a = hits(cat_run, terms=["temperature", "humidity"]).answer
    assert a["text"].startswith("No: no column or value matches “temperature”, “humidity”.") and "The closest columns are" in a["text"] and not a["figures"]
    close = closest_columns(cat_run[4], ["order state"], 2)
    assert close[0].display == "Order state"


@pytest.mark.parametrize("terms,column,value", [(["returnd", "sent back"], "Order state", "returned"), (["delivred"], "Order state", "delivered"), (["mumbay"], "City", "mumbai"), (["canceled"], "Order state", "cancelled"),
                                                  (["envelop"], "Pack kind", "envelope")])
def test_spelling_variants_are_matched(cat_run, terms, column, value):
    a = hits(cat_run, terms=terms).answer
    assert a["text"].startswith("Yes") and f"“{column}”" in a["text"] and f"“{value}”" in a["text"].lower(), a["text"]


def test_four_unrelated_concepts_each_found_with_the_words_a_scripted_planner_supplies(cat_run):
    cases = [("is there anything about sent-back or cancelled orders", ["return", "returned", "cancel", "cancelled", "sent back"], "Order state"),
             ("do you track money paid back", ["refund", "money back", "paid back", "reimbursement"], "Refund reason"),
             ("what kinds of packaging are used", ["packaging", "package", "parcel", "box"], "Pack kind"),
             ("is the town recorded", ["town", "city", "delivery"], "City"),
             ("is there a customer name", ["buyer", "customer", "client"], "Buyer")]
    for q, terms, expected in cases:
        a = ask(cat_run, FakeAI(plan(terms=terms)), q)
        assert a["status"] == "answered" and a["label"] == "About your data" and f"“{expected}”" in a["text"] and a["text"].startswith("Yes"), (q, a["text"])
        assert a["agent"]["tools"] == ["data_catalog"]


def test_a_mostly_empty_column_is_reported_with_its_missing_share(cat_run):
    rid, wd, res, df, ctx = cat_run
    a = hits(cat_run, terms=["refund"]).answer["text"]
    assert "“Refund reason”" in a and "90.0% of its values are missing" in a and "mostly empty" in a
    m = ask(cat_run, FakeAI(plan("missing", column="Refund reason")), "how much of the refund reason is missing")
    k = int(df["refund_reason"].isna().sum())
    assert m["text"] == f"90.0% of “Refund reason” is missing ({k:,} of {len(df):,} rows)." and m["figures"][0]["value"] == k == 108
    allm = ask(cat_run, FakeAI(plan("missing")), "which columns have gaps")
    assert "“Refund reason”" in allm["text"] and allm["table"]["rows"][0][0] == "Refund reason"


def test_columns_and_values_lookups(cat_run):
    rid, wd, res, df, ctx = cat_run
    c = ask(cat_run, FakeAI(plan("columns")), "what columns do I have")
    assert f"{df.shape[1]} columns and {len(df):,} rows" in c["text"] and c["table"]["total_rows"] == df.shape[1]
    v = ask(cat_run, FakeAI(plan("values", column="Order state")), "what values does the state column hold")
    assert v["table"]["rows"][0] == ["delivered", "90", "75.0%"] and "3 distinct values" in v["text"]
    p = ask(cat_run, FakeAI(plan("values", column="Buyer")), "what are the buyers' names")
    assert "I don't list the values" in p["text"] and "Priya" not in json.dumps(p) and p["figures"][0]["value"] == store.load(wd)["turns"][-1]["answer"]["figures"][0]["value"]
    bad = ask(cat_run, FakeAI(plan("values", column="No such thing")), "values of a missing column")
    assert "don't have a column" in bad["text"] or bad["status"] != "answered"


def test_the_ai_never_receives_a_row_or_a_personal_value(cat_run):
    rid, wd, res, df, ctx = cat_run
    ai = FakeAI(plan(terms=["returned"]))
    ask(cat_run, ai, "is there anything about returned orders")
    blob = " ".join(ai.prompts)
    for v in list(df["buyer"].unique()) + ["user3@example.com", "ORD-1005", "+91 9800"]:
        assert v not in blob, v
    assert "returned (18, 15.0%)" in blob and "values not listed" in blob                          # the card is there; the withheld columns have counts only
    assert not any(str(x) in blob for x in df["amount"].unique()[:0]) and "DATA CARD" in blob


# ------------------------------------------------------------------ falling back before refusing
def test_no_plan_or_an_empty_plan_falls_back_to_the_card_and_the_catalog(cat_run):
    q = "does the data have any information about return packages or cancelled ones?"
    for reply in ({"plan": [], "unsupported": ""}, {"plan": "nonsense"}, {"plan": [{"tool": "no_such_tool", "args": {}}]}):
        ai = FakeAI(reply)
        a = ask(cat_run, ai, q)
        assert a["status"] == "answered" and a["text"].startswith("Yes") and "“Order state”" in a["text"] and "cancelled" in a["text"], (reply, a["text"])
        assert a["agent"]["tools"] == ["data_catalog"] and ai.calls <= 2
    refused = ask(cat_run, FakeAI({"plan": [], "unsupported": "no"}), "please book me a flight to the moon")
    assert refused["status"] == "no_fit" and len(refused["chips"]) == 2


def test_a_question_about_a_value_in_the_card_is_answered_from_the_card_with_no_tool(cat_run):
    rid, wd, res, df, ctx = cat_run
    card = datacard.get(ctx, df)
    state = next(d for d in card["columns"] if d["name"] == "state")
    ai = FakeAI({"plan": [], "reply": f"Yes, 18 orders are returned (15.0%) [{state['id']}]."})
    a = ask(cat_run, ai, "are some orders returned?")
    assert ai.calls == 1 and a["agent"]["tools"] == [] and a["label"] == "About your data" and a["text"].startswith("Yes, 18 orders are returned (15.0%)")
    assert a["fact_ids"] == [state["id"]]
    wrong = FakeAI({"plan": [], "reply": f"Yes, 19 orders are returned (15.0%) [{state['id']}]."})
    b = ask(cat_run, wrong, "are some orders returned?")
    assert "19 orders are returned" not in b["text"]                                                         # a figure that is not the card's is not shown as an answer
    nocite = FakeAI({"plan": [], "reply": "Yes, some are returned."})
    assert "Yes, some are returned" not in ask(cat_run, nocite, "are some orders returned?")["text"]            # a reply that cites no line of the card is not an answer


# ------------------------------------------------------------------ a refusal always shows two concrete chips
@pytest.mark.parametrize("q,reply", [("email this to my boss", {"plan": [], "unsupported": "x"}), ("hide the sparkle thing", {"plan": [], "unsupported": "x"}), ("what will sales be in ten years", {"plan": [], "unsupported": "x"}),
                                      ("do something strange", {"plan": [{"tool": "no_such_tool", "args": {}}]}), ("what is the weather", {"plan": "bad"})])
def test_every_refusal_shows_two_chips_and_the_sentence_matches_them(cat_run, q, reply):
    a = ask(cat_run, FakeAI(reply), q)
    assert a["status"] in ("no_fit", "answered") and (a.get("unmet") or a["status"] == "no_fit")
    if a.get("unmet"):
        assert len(a["chips"]) == 2 and all(c["text"] for c in a["chips"])
        assert "Here are two" in a["text"] and "I can answer questions about your data, explain" not in a["text"]               # two chips, not a paragraph of abilities
        for w in ("data_catalog", "view_edit", "plan", "command", "schema", "json", "registry"):
            assert w not in a["text"].lower()
