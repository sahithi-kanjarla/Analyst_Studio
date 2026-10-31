"""The "How this was calculated" box, written once on the server so that what is shown can never be empty.
`lines(how)` turns a `how` record (a data calculation, or a quote from the run's saved records) into the plain lines the page shows."""
from __future__ import annotations

DATA_FOOT = "Every figure is calculated by code from your cleaned data. The AI only chose what to calculate."
RECORD_FOOT = "Every figure is quoted from this run's saved report records or calculated by code. The AI did not calculate anything."


def _n(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def lines(how: dict | None) -> list[str]:
    """Never empty: a missing or bare record still says what kind of answer this was."""
    how = how or {}
    out = [str(how.get("operation") or "Answered from this run's saved records").rstrip(".") + "."]
    if how.get("sources"):
        out.append("Taken from: " + "; ".join(str(s) for s in how["sources"]))
    if how.get("fact_ids"):
        out.append("Checked facts used: " + ", ".join(str(s) for s in how["fact_ids"]))
    if "columns" in how or "rows_total" in how:
        out.append("Columns used: " + (", ".join(str(c.get("label") or c.get("name")) for c in how.get("columns") or []) or "none (rows were counted)"))
        flt = how.get("filters") or []
        out.append("Filters: " + ("; ".join(flt) if flt else "none"))
        if how.get("rows_total") is not None:
            out.append(f"Rows: {_n(how.get('rows_total', 0))} in the data, {_n(how.get('rows_used', 0))} used, {_n(how.get('rows_left_out', 0))} left out")
        for item in how.get("left_out") or []:
            if item.get("rows"):
                out.append(f"{_n(item['rows'])} rows: {item.get('reason', '')}")
        if how.get("groups_not_shown"):
            out.append(f"{_n(how['groups_not_shown'])} more groups are not shown")
    out += [str(n) for n in how.get("notes") or []]
    out.append(RECORD_FOOT if how.get("sources") or how.get("fact_ids") else DATA_FOOT)
    return out


def with_lines(how: dict | None) -> dict:
    h = dict(how or {})
    h["lines"] = lines(h)
    return h


def record_how(operation: str, sources: list[str], fact_ids: list[str] | None = None, notes: list[str] | None = None) -> dict:
    """The `how` of an answer that quotes the run's records rather than calculating on the table."""
    return with_lines({"operation": operation, "sources": sources, "fact_ids": list(dict.fromkeys(fact_ids or [])), "notes": notes or []})
