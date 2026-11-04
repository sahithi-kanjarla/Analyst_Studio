"""The Report reads as a document that can be shared: a one-line scope built by code, and short footnotes only for caveats that change how a figure reads."""
from app import reporter


def _state(**kw):
    prof = {"rows": 1234, "columns": [{"name": "d", "role": "date", "stats": {"min": "2025-01-01", "max": "2025-11-25"}}]}
    return {"profile": prof, "tables": [], "findings": [], **kw}


def test_scope_line_is_built_from_the_profile():
    assert reporter._scope_line(_state()) == "Based on 1,234 records, 01 Jan 2025 to 25 Nov 2025, from 1 file"


def test_scope_line_says_orders_only_when_an_order_key_was_verified():
    st = _state()
    st["profile"]["grain"] = {"orders": 400, "key": "k"}
    assert reporter._scope_line(st).startswith("Based on 400 orders, ")


def test_scope_line_counts_files_and_survives_no_dates():
    st = _state(tables=[{"name": "a"}, {"name": "b"}, {"name": "c"}])
    st["profile"]["columns"] = []
    assert reporter._scope_line(st) == "Based on 1,234 records, from 3 files"
    assert reporter._scope_line({"profile": {}}) == ""


def test_footnote_for_a_partial_last_month_names_the_days_covered():
    f = {"stats": {"partial": {"label": "2025-11-01", "days_in_period": 30, "days": 25, "partial": True}}}
    out = reporter._footnotes(_state(findings=[f, f]))
    assert len(out) == 1 and "25 of 30 days" in out[0]


def test_footnote_for_a_period_the_person_said_really_ended():
    f = {"stats": {"partial": {"label": "2025-11-01", "days_in_period": 30, "days": 25, "partial": True, "really_ended": True}}}
    assert "You said" in reporter._footnotes(_state(findings=[f]))[0]


def test_no_footnote_when_nothing_changes_how_a_number_reads():
    f = {"stats": {"partial": {"label": "2025-11-01", "days_in_period": 30, "days": 30, "partial": False}}}
    assert reporter._footnotes(_state(findings=[f, {"stats": {}}])) == []
