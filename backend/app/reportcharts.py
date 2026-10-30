"""Charts inside the report page. The facts an answer card uses offer a closed list of chart specs; the AI picks which of them best answers the question and writes the
"how to read this" caption (reporter does that call); code draws every chart from the computed numbers. Nothing here analyses data: a spec is a view of numbers a fact already holds.

  ranking fact        bar chart, top BARS_MAX groups
  outside-rate fact   bars of the split of the amount outside the main value, and bars of the rate for each breakdown (the widest spread first)
  growth fact         bars of the change per group
  trend finding       monthly bars (a month with no data is left blank), or a line for days and weeks"""
from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import summary  # noqa: E402
from .config import CHART_DPI  # noqa: E402
from .tools import PALETTE  # noqa: E402

MAX_CHARTS = 8          # distinct charts on the page
PER_CARD = 2
CAPTION_WORDS = 20
FREQ = {"D": "D", "W": "W", "MS": "MS"}


def _spec(sid: str, fact: str, kind: str, title: str, data: list, **extra) -> dict:
    return {"id": sid, "fact": fact, "kind": kind, "title": title, "data": data, **extra}


def specs_for(ids: list[str], by_id: dict, r_by_id: dict, state: dict) -> list[dict]:
    """Every chart the facts `ids` can draw, best first (for a loss fact, the breakdown with the widest spread first). The AI may only choose among these ids."""
    out: list[dict] = []
    for i in ids:
        r = r_by_id.get(i)
        if r:
            cur = summary._cur(r["measure"], state) if r["measure"] != "rows" else ""
            if r.get("kind") == "outside_rate":
                for b in sorted(r.get("by") or [], key=lambda b: -b.get("spread", 0)):
                    if b.get("bars"):
                        out.append(_spec(f"{i}:by:{b['group']}", i, "bars", f"Share of {r['measure']} outside {r['top']}, by {b['group']}", b["bars"], pct=True))
                if r.get("split"):
                    out.append(_spec(f"{i}:split", i, "bars", f"{r['measure']} outside {r['top']}, by {r['group']}", [[x["value"], x["amount"]] for x in r["split"][:summary.BARS_MAX]], cur=cur))
            elif r.get("kind") == "growth":
                rows = sorted(r["groups"], key=lambda g: -abs(g["change"]))[:summary.BARS_MAX]
                out.append(_spec(f"{i}:growth", i, "bars", f"Change in {r['measure']} by {r['group']}, {r['periods'][0]} to {r['periods'][1]}", [[g["group"], g["change"]] for g in rows], cur=cur))
            elif r.get("bars"):
                what = f"Total {r['measure']}" if r["basis"] == "total" else str(r["measure"])
                out.append(_spec(f"{i}:bars", i, "bars", f"{what} by {r['group']}", r["bars"], cur=cur if r["basis"] == "total" else "", pct=bool(r.get("bars_pct"))))
        elif (f := by_id.get(i)) and f.get("tool") == "trend" and f.get("series"):
            st, target = f.get("stats") or {}, str((f.get("params") or {}).get("target") or "")
            word = {"sum": "Total", "mean": "Average"}.get(st.get("agg"), "")
            unit = {"D": "day", "W": "week", "MS": "month"}.get(st.get("freq"), "period")
            out.append(_spec(f"{i}:series", i, "series", f"{word} {target} per {unit}".strip(), f["series"], freq=FREQ.get(st.get("freq"), "MS"), cur=summary._cur(target, state)))
    return out


def fallback_caption(spec: dict) -> str:
    """A caption with no figure in it, for when the AI wrote none or its caption failed the figure check."""
    if spec["kind"] == "series":
        return "Each point is one period; a gap means no data in that period."
    return "Bars show each group, largest first; the longer the bar, the larger the value."


def _axis(spec: dict):
    cur = spec.get("cur") or ""
    if spec.get("pct"):
        return lambda v, _=None: f"{v:.0%}" if abs(v) < 1 else f"{v:.0f}%"
    return lambda v, _=None: summary.short(v, cur)


def draw_path(spec: dict) -> str:
    return f"charts/chart_{hashlib.md5(spec['id'].encode()).hexdigest()[:10]}.png"


def draw(spec: dict, charts_dir) -> str:
    """Draw one spec to <charts_dir>/<hash>.png and return the page-relative path. The file name is a hash of the spec id, so a chart is drawn once however often it is used."""
    name = draw_path(spec)[len("charts/"):-len(".png")]
    path = Path(charts_dir)
    path.mkdir(parents=True, exist_ok=True)
    fmt = _axis(spec)
    fig, ax = plt.subplots(figsize=(6.4, 3.0 if spec["kind"] == "series" else max(2.2, 0.32 * len(spec["data"]) + 0.9)))
    if spec["kind"] == "series":
        idx = pd.to_datetime([d for d, _ in spec["data"]])
        s = pd.Series([v for _, v in spec["data"]], index=idx, dtype="float64")
        monthly = spec.get("freq", "MS") == "MS"
        s = s.reindex(pd.date_range(idx.min(), idx.max(), freq=spec.get("freq", "MS")))          # periods with no rows become NaN: blank, never zero
        if monthly:
            ax.bar(range(len(s)), s.values, color=PALETTE[0], width=0.7)          # a month with no data has no bar
            step = max(1, len(s) // 8)
            ax.set_xticks(range(0, len(s), step), [d.strftime("%Y-%m") for d in s.index[::step]], rotation=30, ha="right", fontsize=8)
        else:
            ax.plot(s.index, s.values, color=PALETTE[0], marker="o", ms=3, lw=1.6)         # NaN breaks the line: an empty period stays blank
            fig.autofmt_xdate()
        ax.yaxis.set_major_formatter(fmt)
    else:
        labels = [str(g) for g, _ in spec["data"]][::-1]
        vals = [v for _, v in spec["data"]][::-1]
        ax.barh(labels, vals, color=PALETTE[0])
        ax.xaxis.set_major_formatter(fmt)
        ax.tick_params(axis="y", labelsize=8)
        ax.axvline(0, color="#888", lw=0.8)
    ax.set_title(spec["title"], fontsize=10, loc="left")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.savefig(path / f"{name}.png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f"charts/{name}.png"
