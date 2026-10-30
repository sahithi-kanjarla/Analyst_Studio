"""Charts inside the report page. The facts an answer card uses offer a closed list of chart specs; the AI picks which of them best answers the question and writes the
"how to read this" caption (reporter does that call); code draws every chart from the computed numbers. Nothing here analyses data: a spec is a view of numbers a fact already holds.

  ranking fact        bar chart, top BARS_MAX groups
  outside-rate fact   bars of the split of the amount outside the main value, and bars of the rate for each breakdown (the widest spread first)
  growth fact         bars of the change per group
  trend finding       monthly bars (a month with no data is left blank), or a line for days and weeks

Titles and axis labels use the display names of the understanding record, never an internal name. Every bar shows its value at its end."""
from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import summary  # noqa: E402
from . import understanding as und  # noqa: E402
from .config import CHART_DPI  # noqa: E402
from .tools import PALETTE  # noqa: E402

MAX_CHARTS = 8          # distinct charts on the page
PER_CARD = 2
CAPTION_WORDS = 20
LABELS_MAX = 12          # a series with more bars than this is not labelled bar by bar
NAME_CHARS = 22
FREQ = {"D": "D", "W": "W", "MS": "MS"}
# The app's own look (frontend styles.css: its ink and line colours; the font is the one tools.py sets from the app's font list). The picture carries its own white panel,
# so dark text on it reads the same on the light page and the dark page.
INK, MUTED, LINE, PANEL = "#0f172a", "#5b667a", "#dfe4ec", "#ffffff"


def _spec(sid: str, fact: str, kind: str, title: str, data: list, **extra) -> dict:
    return {"id": sid, "fact": fact, "kind": kind, "title": title, "data": data, **extra}


def specs_for(ids: list[str], by_id: dict, r_by_id: dict, state: dict) -> list[dict]:
    """Every chart the facts `ids` can draw, best first (for a loss fact, the breakdown with the widest spread first). The AI may only choose among these ids."""
    out: list[dict] = []

    def nice(text) -> str:          # an internal name (underscores) becomes its display name from the understanding record
        return und.readable(str(text), state)
    for i in ids:
        r = r_by_id.get(i)
        if r:
            cur = summary._cur(r["measure"], state) if r["measure"] != "rows" else ""
            if r.get("kind") == "outside_rate":
                for b in sorted(r.get("by") or [], key=lambda b: -b.get("spread", 0)):
                    if b.get("bars"):
                        out.append(_spec(f"{i}:by:{b['group']}", i, "bars", nice(f"Share of {r['measure']} outside {r['top']}, by {b['group']}"), b["bars"], pct=True,
                                         value_label=nice(f"Share of {r['measure']} outside {r['top']}"), group_label=nice(b["group"])))
                if r.get("split"):
                    out.append(_spec(f"{i}:split", i, "bars", nice(f"{r['measure']} outside {r['top']}, by {r['group']}"), [[x["value"], x["amount"]] for x in r["split"][:summary.BARS_MAX]], cur=cur,
                                     value_label=nice(f"{r['measure']} outside {r['top']}"), group_label=nice(r["group"])))
            elif r.get("kind") == "growth":
                rows = sorted(r["groups"], key=lambda g: -abs(g["change"]))[:summary.BARS_MAX]
                out.append(_spec(f"{i}:growth", i, "bars", nice(f"Change in {r['measure']} by {r['group']}, {r['periods'][0]} to {r['periods'][1]}"), [[g["group"], g["change"]] for g in rows], cur=cur,
                                 value_label=nice(f"Change in {r['measure']}"), group_label=nice(r["group"])))
            elif r.get("bars"):
                what = f"Total {r['measure']}" if r["basis"] == "total" else str(r["measure"])
                out.append(_spec(f"{i}:bars", i, "bars", nice(f"{what} by {r['group']}"), r["bars"], cur=cur if r["basis"] == "total" else "", pct=bool(r.get("bars_pct")),
                                 value_label=nice(what), group_label=nice(r["group"])))
        elif (f := by_id.get(i)) and f.get("tool") == "trend" and f.get("series"):
            st, target = f.get("stats") or {}, str((f.get("params") or {}).get("target") or "")
            word = {"sum": "Total", "mean": "Average"}.get(st.get("agg"), "")
            unit = {"D": "day", "W": "week", "MS": "month"}.get(st.get("freq"), "period")
            out.append(_spec(f"{i}:series", i, "series", nice(f"{word} {target} per {unit}".strip()), f["series"], freq=FREQ.get(st.get("freq"), "MS"), cur=summary._cur(target, state),
                             value_label=nice(f"{word} {target}".strip()), group_label=""))
    return out


def _value_fmt(spec: dict):
    """One way to write a figure of this chart, for the bar labels and the caption: a rate as 6.1%, an amount as 17.5M."""
    cur = spec.get("cur") or ""
    if spec.get("pct"):
        return lambda v: f"{v:.1%}" if abs(v) < 1 else f"{v:.1f}%"
    return lambda v: summary.short(v, cur)


def _name(x) -> str:
    text = " ".join(str(x).split())
    return text if len(text) <= NAME_CHARS else text[:NAME_CHARS - 1].rstrip() + "…"


def fallback_caption(spec: dict) -> str:
    """The caption code writes from the chart's own numbers, for when the AI wrote none or its caption failed the figure check: the highest and the lowest point with their figures, and
    whether the gap between them is small or large (summary.spread_of, the test the answers use). At most CAPTION_WORDS words."""
    data = [(g, v) for g, v in spec.get("data") or [] if v is not None and v == v]
    if not data:
        return "There is no data to show."
    fmt, series = _value_fmt(spec), spec["kind"] == "series"
    if series:
        when = "%Y-%m" if spec.get("freq", "MS") == "MS" else "%Y-%m-%d"
        data = [(pd.Timestamp(g).strftime(when), v) for g, v in data]
    top, bottom = max(data, key=lambda x: x[1]), min(data, key=lambda x: x[1])
    if len(data) < 2:
        return f"Only {_name(top[0])} is shown, at {fmt(top[1])}."
    gap = "small" if summary.spread_of(bottom[1], top[1], rate=bool(spec.get("pct")))["similar"] else "large"
    hi, lo = ("Peak", "low") if series else ("Highest", "lowest")
    return f"{hi}: {_name(top[0])} at {fmt(top[1])}; {lo}: {_name(bottom[0])} at {fmt(bottom[1])}. The gap is {gap}."


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
    fmt, label = _axis(spec), _value_fmt(spec)
    look = {"text.color": INK, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": LINE, "figure.facecolor": PANEL, "axes.facecolor": PANEL}
    with plt.rc_context(look):
        fig, ax = plt.subplots(figsize=(6.4, 3.0 if spec["kind"] == "series" else max(2.2, 0.32 * len(spec["data"]) + 0.9)))
        if spec["kind"] == "series":
            idx = pd.to_datetime([d for d, _ in spec["data"]])
            s = pd.Series([v for _, v in spec["data"]], index=idx, dtype="float64")
            monthly = spec.get("freq", "MS") == "MS"
            s = s.reindex(pd.date_range(idx.min(), idx.max(), freq=spec.get("freq", "MS")))          # periods with no rows become NaN: blank, never zero
            if monthly:
                bars = ax.bar(range(len(s)), s.values, color=PALETTE[0], width=0.7)          # a month with no data has no bar
                step = max(1, len(s) // 8)
                ax.set_xticks(range(0, len(s), step), [d.strftime("%Y-%m") for d in s.index[::step]], rotation=30, ha="right", fontsize=8)
                if len(s) <= LABELS_MAX:
                    ax.bar_label(bars, labels=["" if v != v else label(v) for v in s.values], fontsize=7, color=INK, padding=2)
            else:
                ax.plot(s.index, s.values, color=PALETTE[0], marker="o", ms=3, lw=1.6)         # NaN breaks the line: an empty period stays blank
                fig.autofmt_xdate()
            ax.yaxis.set_major_formatter(fmt)
            ax.set_ylabel(spec.get("value_label") or "", fontsize=9)
            ax.margins(y=0.12)
        else:
            labels = [str(g) for g, _ in spec["data"]][::-1]
            vals = [v for _, v in spec["data"]][::-1]
            bars = ax.barh(labels, vals, color=PALETTE[0])
            ax.bar_label(bars, labels=[label(v) for v in vals], fontsize=8, color=INK, padding=3)          # the value at the end of each bar
            ax.xaxis.set_major_formatter(fmt)
            ax.tick_params(axis="y", labelsize=8)
            ax.axvline(0, color=MUTED, lw=0.8)
            ax.set_xlabel(spec.get("value_label") or "", fontsize=9)
            ax.set_ylabel(spec.get("group_label") or "", fontsize=9)
            ax.margins(x=0.16)
        ax.set_title(spec["title"], fontsize=10, loc="left", fontweight="bold", color=INK)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        fig.savefig(path / f"{name}.png", dpi=CHART_DPI, bbox_inches="tight", facecolor=PANEL)
        plt.close(fig)
    return f"charts/{name}.png"
