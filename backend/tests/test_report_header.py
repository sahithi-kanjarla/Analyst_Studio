"""The Report header: title and answer sentence built by code from saved ranking facts; the hidden-items note is never printed. No AI."""
from pathlib import Path

from app import headline

ROOT = Path(__file__).resolve().parents[2]


def fact(bars, measure="Profit"):
    total = sum(v for _, v in bars)
    return {"id": "r1", "basis": "total", "group": "Category", "measure": measure, "bars": [[k, v] for k, v in bars], "top_share": max(v for _, v in bars) / total}


def state(bars, **kw):
    return {"summary_facts": {"rankings": [fact(bars)]}, "curation": {"mode": "overview"}, **kw}


def clean(text, *raw):
    assert headline.repeated_word(text) is None, text
    assert "Top " not in text and "category" not in text.lower() and "by total" not in text, text
    for w in raw:
        assert w not in text, text


def test_top_two_equal_and_third():
    s = headline.build(state([("Clothing", 13325), ("Electronics", 13162), ("Furniture", 10476)]))
    assert s["answer"] == "Clothing and Electronics each account for about 36% of profit (13.3K and 13.2K); Furniture adds 28% (10.5K)."
    assert s["title"] == "Profit overview" and s["tag"] == "Overview, no goal given"
    clean(s["answer"])


def test_one_clear_leader():
    a = headline.build(state([("Home Office", 6000), ("Corporate", 3000), ("Consumer", 1000)]))["answer"]
    assert a == "Home Office accounts for about 60% of profit (6K); Corporate adds 30% (3K) and Consumer 10% (1K)."
    clean(a)


def test_top_three_equal_and_two_groups():
    a = headline.build(state([("A", 100), ("B", 100), ("C", 100)]))["answer"]
    assert a.startswith("A, B and C each account for about 33% of profit") and "adds" not in a
    b = headline.build(state([("North", 750), ("South", 250)]))["answer"]
    assert b == "North accounts for about 75% of profit (750); South adds 25% (250)."


def test_display_name_and_no_goal_tag_only_for_overview():
    st = state([("A", 70), ("B", 30)], understanding={"columns": {"Profit": {"display_name": "Net VAT"}}},
               profile={"columns": [{"name": "Profit"}]})
    st["curation"] = {"mode": "goal"}
    s = headline.build(st)
    assert "of net VAT" in s["answer"] and s["tag"] == "" and s["title"] == "Net VAT overview"


def test_no_sentence_without_a_usable_fact():
    assert headline.build({"summary_facts": {"rankings": [fact([("A", 5), ("B", -1)])]}})["answer"] == ""
    assert headline.build({"goal_answer": {"sentence": "x"}, **state([("A", 5), ("B", 4)])})["answer"] == ""
    assert headline.build({})["title"] == "Data overview"


def test_hidden_items_note_is_not_printed():
    page = (ROOT / "frontend/src/components/ReportPage.tsx").read_text(encoding="utf8")
    assert 'className="view-note no-print"' in page
    assert ".no-print" in (ROOT / "frontend/src/print.css").read_text(encoding="utf8").split("@media print")[1]
    assert "@media print { .view-note { display: none" in (ROOT / "frontend/src/report.css").read_text(encoding="utf8")
