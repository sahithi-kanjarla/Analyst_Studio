"""Redraws one chart of the report another way (chart type, time grain, top N) when the user asks, from the finding behind it, through the same checked Lens query and chart code
as every Lens answer. The report's own chart, text and figures are not touched: the redrawn picture is a separate file, labelled as redrawn."""
from __future__ import annotations

from pathlib import Path

from . import engine, query as qmod, render

TYPE_TO_LENS = {"bar": "bar", "line": "line", "share": "share", "table": "table", "histogram": "histogram"}


def base_query(finding: dict, override: dict, ctx) -> dict | None:
    """The Lens query that reproduces what the finding charts, with the user's change applied. None when the finding cannot be redrawn from a simple query."""
    p, text = finding.get("params") or {}, str(finding.get("text") or "")
    agg = "sum" if "sum per" in text or finding.get("tool") == "trend" else "average"
    chart = TYPE_TO_LENS.get(override.get("chart") or "", "auto")
    if finding.get("tool") == "group_compare" and p.get("group") and p.get("target"):
        return {"op": "top", "measure": p["target"], "agg": agg, "group_by": [p["group"]], "n": int(override.get("n") or 8), "direction": "top", "chart": chart if chart != "auto" else "bar"}
    if finding.get("tool") == "trend" and p.get("target"):
        q = {"op": "trend", "measure": p["target"], "agg": agg, "grain": override.get("grain") or "month", "chart": chart if chart in ("line", "bar", "table") else "line"}
        if p.get("date") or ctx.date_col:
            q["date"] = p.get("date") or ctx.date_col
        return q
    return None


def render_override(wd: Path, result: dict, finding: dict, override: dict, df, ctx) -> dict | None:
    """{"path": "charts/lens_<key>.png", "note": str} or None when it cannot be drawn (the original chart is then kept)."""
    q = base_query(finding, override, ctx)
    if not q:
        return None
    ch = qmod.check(q, ctx, df)
    if not ch.query:
        return None
    try:
        R = engine.run(ch.query, df, ctx)
    except engine.Cannot:
        return None
    png = render.chart_file(wd, R.query, R.chart)
    if not (png.exists() or render.draw(R, png)):
        return None
    return {"path": f"charts/{png.name}", "note": "Redrawn by Lens from your cleaned data. The report's own chart and figures are unchanged."}
