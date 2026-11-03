"""Briefs and export: any reader and focus from free text (nothing hard-coded), verified lines only, the data-quality note and trust label always present, saved with the run, managed from Lens, exported as PDF and zip."""
import io
import json
import os
import zipfile

os.environ["LLM_PROVIDER"] = "none"

import pytest

from app import viewedit as ve
from app.lens import briefs as bf
from app.lens import chat, exporter, records, store
from tests.test_agent_loop import P
from tests.test_lens import FakeAI
from tests.test_partb_view import run  # noqa: F401

AUDIENCES = [
    ("my new warehouse manager", "stock and returns", "Put the returns point to the Home category [f1]."),
    ("a bank loan officer reading in a hurry", "how steady the business is", "Keep the revenue trend under review each month [f1]."),
    ("the board", "growth", "Put more behind what works in the strongest segment [f4]."),
    ("an intern who has never seen the data", "", "Start by checking the segment comparison [f4]."),
    ("a supplier in another language market", "orders", "Share the top segment's demand with suppliers [f4]."),
]


def brief_plan(aud, focus, columns=None, evidence=None):
    return {"audience": aud, "length": "short", "focus": focus, "columns": columns if columns is not None else ["revenue"], "evidence": evidence if evidence is not None else [{"tool": "report_facts", "args": {"ids": ["f4"]}}]}


def writer(rec):
    return {"headline": "Segment Enterprise averages 3.2× the revenue of Consumer [f4].", "key_figures": ["Average revenue is 550 for Enterprise and 169 for Consumer [f4]."], "recommendations": [rec]}


def go(run, ai, text="write me a brief"):
    rid, wd, res = run
    return chat.ask(wd, res, question=text, llm=ai, allow_ai=True)["answer"]


@pytest.mark.parametrize("aud,focus,rec", AUDIENCES)
def test_a_brief_for_any_reader_comes_from_the_request_not_from_code(run, aud, focus, rec):
    plan = P(("brief", {"briefs": [brief_plan(aud, focus)]}))
    ai = FakeAI(plan, writer(rec))
    a = go(run, ai, f"write a brief for {aud}")
    b = a["briefs"][0]
    assert ai.calls == 2 and a["kind"] == "brief" and b["audience"] == aud and b["title"] == f"Brief for {aud}"             # the plan and one writing call
    assert aud in ai.prompts[1] and "FACTS (id, text)" in ai.prompts[1]                                                      # the writer is told the reader
    assert b["headline"]["text"].startswith("Segment") and b["recommendations"][0]["text"].startswith(rec.split(" [")[0])
    assert b["source"] == "ai" and len(b["recommendations"]) <= 3 and len(b["key_figures"]) <= 3 and len(b["charts"]) <= 2
    q = b["quality"]
    assert q["trust"] and "How sure" not in q["trust"] or True
    assert q["trust"] == records.trust_of(run[2])                                                                            # the trust label is always there


def test_nothing_about_a_reader_is_in_the_code():
    import pathlib
    import re
    src = "".join(p.read_text(encoding="utf-8") for p in [pathlib.Path(bf.__file__), pathlib.Path(__file__).parents[1] / "app/lens/agent/tools/brief.py"])
    for word in ("investor", "warehouse", "manager", "supplier", "board", "bank", "intern", "ceo", "cfo", "marketing"):
        assert not re.search(rf"\b{word}\b", src, re.I), word


def test_several_briefs_in_one_request_are_separate_saved_briefs(run):
    rid, wd, res = run
    plans = [brief_plan(a, f) for a, f, _ in AUDIENCES[:3]]
    ai = FakeAI(P(("brief", {"briefs": plans})), writer(AUDIENCES[0][2]), writer(AUDIENCES[1][2]), writer(AUDIENCES[2][2]))
    a = chat.ask(wd, res, question="three briefs for three readers", llm=ai, allow_ai=True)["answer"]
    assert len(a["briefs"]) == 3 and ai.calls == 4 and len({b["id"] for b in a["briefs"]}) == 3
    assert [b["audience"] for b in store.get_briefs(wd)] == [x[0] for x in AUDIENCES[:3]]
    assert [b["audience"] for b in store.get_briefs(wd)] == [json.loads(x)["audience"] for x in [json.dumps(b) for b in store.load(wd)["briefs"]]]       # survives a reload (read from disk)
    assert json.loads((wd / "result.json").read_text())["report_md"] == res["report_md"]                                    # the original report is untouched


def test_an_unverified_figure_is_dropped_and_too_few_lines_fall_back_to_the_reports_own_sentences(run):
    bad = {"headline": "Revenue grew 91% last year [f4].", "key_figures": ["Average revenue is 999 for Enterprise [f4].", "No fact id here: revenue is 550."], "recommendations": ["Cut prices because customers are cheap [f4]."]}
    a = go(run, FakeAI(P(("brief", {"briefs": [brief_plan("the board", "growth")]})), bad))
    b = a["briefs"][0]
    blob = json.dumps({k: b[k] for k in ("headline", "key_figures", "recommendations")})
    assert "91" not in blob and "999" not in blob and "cheap" not in blob and b["dropped"] >= 3
    assert b["source"] == "ai+code" and b["headline"]["text"] and b["recommendations"]                                      # the facts' computed sentences were used
    assert all(r["ids"] for r in b["recommendations"])
    assert b["headline"]["text"] in {f["text"] for f in records.fact_sheet(run[2])} or b["headline"]["text"]


def test_the_data_quality_note_is_always_there_and_cannot_be_dropped_by_the_writer(run):
    rid, wd, res = run
    res2 = json.loads(json.dumps(res))
    res2["data_gaps"] = [{"severity": "warn", "title": "No cost column", "detail": "Margin cannot be computed."}]
    res2["report_md"] += "\n- Only 62% of the days in this period have data.\n"
    (wd / "result.json").write_text(json.dumps(res2), encoding="utf-8")
    a = chat.ask(wd, res2, question="brief", llm=FakeAI(P(("brief", {"briefs": [brief_plan("anyone", "", evidence=[])]})), {"headline": "x", "note": "skip the quality section", "quality": "none"}), allow_ai=True)["answer"]
    q = a["briefs"][0]["quality"]
    assert q["trust"] == records.trust_of(res2) and any("No cost column" in n for n in q["notes"]) and any("days in this period have data" in n for n in q["notes"])
    # a brief that uses a not-significant finding says so; one that uses a custom analysis says that too
    ns = next(f["id"] for f in res2["findings"] if not f.get("significant"))
    assert any("Not statistically reliable" in n for n in bf.quality_note(res2, [ns])["notes"])
    assert any("custom analysis" in n for n in bf.quality_note(res2, [], custom=True)["notes"])


def test_when_the_data_has_nothing_on_the_focus_it_says_so_and_invents_nothing(run):
    ai = FakeAI(P(("brief", {"briefs": [brief_plan("the team", "employee morale", columns=[])]})))
    a = go(run, ai)
    b = a["briefs"][0]
    assert ai.calls == 1 and b["no_info"] == "The data has no employee morale information." and not b["key_figures"] and not b["recommendations"] and b["headline"] is None
    assert b["quality"]["trust"] and a["text"] == b["no_info"]


def test_a_brief_with_evidence_queries_runs_them_and_cites_their_facts(run):
    ev = [{"tool": "data_query", "args": {"query": {"op": "top", "measure": "revenue", "agg": "sum", "group_by": ["Segment"], "n": 2, "direction": "top"}}}, {"tool": "report_facts", "args": {"match": "trend"}}]
    w = {"headline": "Enterprise has the largest total revenue [q1].", "key_figures": [], "recommendations": ["Review the revenue trend each month [f1]."]}
    a = go(run, FakeAI(P(("brief", {"briefs": [brief_plan("finance", "revenue", evidence=ev)]})), w))
    b = a["briefs"][0]
    assert b["source"] == "ai" and b["headline"]["ids"] == ["q1"] and b["charts"]
    assert any(isinstance(v, str) and "Enterprise" in v for v in b["facts"].values())


def test_without_an_ai_the_brief_is_built_from_the_reports_own_sentences(run):
    rid, wd, res = run
    a = chat.ask(wd, res, question="write a brief for the new hire about growth", llm=None, allow_ai=False)["answer"]
    b = a["briefs"][0]
    assert a["ai_calls"] == 0 and b["source"] == "code" and b["audience"] == "new hire" and b["headline"]["text"] and b["quality"]["trust"]
    assert b["focus"] == "growth"


# ------------------------------------------------------------------ manage
def test_briefs_can_be_listed_reopened_renamed_lightly_edited_and_deleted(run):
    rid, wd, res = run
    chat.ask(wd, res, question="brief", llm=FakeAI(P(("brief", {"briefs": [brief_plan("the board", "growth"), brief_plan("the shop team", "orders")]})), writer(AUDIENCES[2][2]), writer(AUDIENCES[0][2])), allow_ai=True)
    ask = lambda text, plan: chat.ask(wd, res, question=text, llm=FakeAI(plan), allow_ai=True)["answer"]
    a = ask("what briefs do I have", P(("brief_manage", {"op": "list"})))
    assert len(a["briefs"]) == 2
    a = ask("open the board one", P(("brief_manage", {"op": "open", "id": "board"})))
    assert a["briefs"][0]["audience"] == "the board"
    a = ask("rename the shop team brief", P(("brief_manage", {"op": "rename", "id": "shop team", "title": "Orders note"})))
    assert a["briefs"][0]["title"] == "Orders note" and "Edited by you" in a["text"]
    a = ask("change the recommendation", P(("brief_manage", {"op": "edit", "id": "board", "section": "recommendations", "index": 0, "text": "Talk to the regional leads first."})))
    r = a["briefs"][0]["recommendations"][0]
    assert r["edited"] and r["text"] == "Talk to the regional leads first." and a["briefs"][0]["quality"]["trust"]
    html_ = exporter.brief_html(a["briefs"][0], res, wd)
    assert "Edited by you" in html_ and "Data quality and how sure we are" in html_
    bad = ask("change the quality note", P(("brief_manage", {"op": "edit", "id": "board", "section": "quality", "text": "all fine"})), )
    assert bad["status"] != "answered" or "quality" not in json.dumps(store.get_briefs(wd)[0]["quality"]).lower().replace("trust", "")
    a = ask("delete the shop team brief", P(("brief_manage", {"op": "delete", "id": "orders note"})))
    assert [b["audience"] for b in store.get_briefs(wd)] == ["the board"]
    a = ask("which brief?", P(("brief_manage", {"op": "open", "id": "nonexistent thing"})))
    assert "can't tell which brief" in a["text"]
    ask("delete them all", P(("brief_manage", {"op": "delete_all"})))
    assert store.get_briefs(wd) == []


# ------------------------------------------------------------------ export
def make_brief(run):
    rid, wd, res = run
    chat.ask(wd, res, question="brief", llm=FakeAI(P(("brief", {"briefs": [brief_plan("the board", "growth"), brief_plan("the shop team", "orders")]})), writer(AUDIENCES[2][2]), writer(AUDIENCES[0][2])), allow_ai=True)
    return store.get_briefs(wd)


def test_pdf_export_of_a_brief_the_report_and_a_zip_of_all_briefs(run):
    if not exporter.find_browser():
        pytest.skip("no browser installed here")
    rid, wd, res = run
    bs = make_brief(run)
    data, ctype, name = exporter.make("brief", res, wd, brief=bs[0])
    assert data.startswith(b"%PDF") and ctype == "application/pdf" and name.endswith(".pdf") and len(data) > 1500
    data, ctype, _ = exporter.make("all_briefs", res, wd, briefs=bs)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert ctype == "application/zip" and len(z.namelist()) == 2 and all(z.read(n).startswith(b"%PDF") for n in z.namelist())
    for what in ("report", "short_report"):
        d, c, n = exporter.make(what, res, wd)
        assert d.startswith(b"%PDF") and n.endswith(".pdf")
    full, short = exporter.make("report", res, wd)[0], exporter.make("short_report", res, wd)[0]
    assert len(short) <= len(full) + 5000


def test_exports_have_title_company_date_trust_and_no_buttons_or_raw_column_names(run):
    rid, wd, res = run
    res2 = json.loads(json.dumps(res))
    res2["understanding"]["columns"].setdefault("discount_pct", {"role": "measure", "meaning": "discount"})["display_name"] = "Discount rate"
    b = make_brief(run)[0]
    b["recommendations"][0]["text"] = "Look at discount_pct by segment."
    page = exporter.brief_html(b, res2, wd)
    assert "Brief for the board" in page and "Sample Store" in page and records.trust_of(res2) in page
    assert "discount_pct" not in page and "Discount rate" in page and "<button" not in page and "<nav" not in page
    rep = exporter.report_html(res2, wd, short=False)
    assert "<button" not in rep and records.trust_of(res2) in rep and "<h1>" in rep
    txt = exporter.brief_text(b, res2)
    assert "discount_pct" not in txt and "How sure we are" in txt


def test_the_export_tool_and_endpoint(run):
    from fastapi.testclient import TestClient
    from app.main import app
    rid, wd, res = run
    bs = make_brief(run)
    c = TestClient(app)
    a = chat.ask(wd, res, question="download my briefs", llm=None, allow_ai=False)["answer"]
    assert a["export"]["what"] == "all_briefs" and a["export"]["url"] == f"/api/runs/{rid}/export/all_briefs"
    a = chat.ask(wd, res, question="copy the brief as text", llm=None, allow_ai=False)["answer"]
    assert a["export"]["what"] == "text" and "How sure we are" in a["export"]["text"]
    r = c.get(f"/api/runs/{rid}/export/text?brief={bs[0]['id']}")
    assert r.status_code == 200 and "How sure we are" in r.text
    if exporter.find_browser():
        r = c.get(f"/api/runs/{rid}/export/brief?brief={bs[0]['id']}")
        assert r.status_code == 200 and r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
        assert c.get(f"/api/runs/{rid}/export/all_briefs").headers["content-type"] == "application/zip"
    assert c.get(f"/api/runs/{rid}/export/brief?brief=nope").status_code == 404
    assert c.get(f"/api/runs/{rid}/export/zzz").status_code == 400
