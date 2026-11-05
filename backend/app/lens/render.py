"""Draws a Lens answer as a PNG with matplotlib (no AI, no browser). The same look as the report's charts: one accent colour, quiet greys, values written on the marks."""
from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib
from matplotlib.figure import Figure

from .. import chartfinish, palette

matplotlib.rcParams.update({**palette.rc(), **chartfinish.RC})      # the same look whether or not tools.py has been imported
from ..config import CHART_DPI
from . import query as qmod
from .engine import Result

# One shared palette (app/palette.py, generated from the chart-tokens block of tokens.css).
ACCENT, GREY, INK = palette.ACCENT, palette.NEUTRAL, palette.INK
SERIES = palette.CATEGORICAL
GRID, PANEL, MARK = palette.GRID, palette.PANEL, palette.BAD


def _style(ax, title: str) -> None:
    ax.set_title(title, fontsize=12, loc="left", color=INK, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=9.5, colors=INK)


def _short(v: float) -> str:
    a = abs(v)
    return f"{v / 1e9:.3g}B" if a >= 1e9 else f"{v / 1e6:.3g}M" if a >= 1e6 else f"{v / 1e3:.3g}K" if a >= 1e4 else f"{v:,.4g}"


def _trim(label: str, n: int = 22) -> str:
    return label if len(label) <= n else label[:n - 1] + "…"


def draw(R: Result, path: Path) -> bool:
    """Writes the PNG for R.chart. False when the result is shown as a table only (no picture)."""
    if R.chart == "table" or not R.plot:
        return False
    P, fig = R.plot, Figure(figsize=(7.2, 4.2), dpi=CHART_DPI)
    ax = fig.add_subplot()
    kind = P["kind"]
    fmt = P.get("fmt") or _short
    if kind == "bars" and R.chart == "line":
        P, kind = {**P, "x": P["labels"]}, "series"
    if kind == "share" and not (R.chart == "share" and len(P["values"]) <= 7 and min(P["values"]) >= 0):
        P, kind = {**P, "series": [P["bar_series"]], "horizontal": True}, "bars"
    if kind == "share":
        vals, labels = P["values"], P["labels"]
        w, _ = ax.pie(vals, labels=None, colors=[SERIES[i % len(SERIES)] if l != "All others" else GREY for i, l in enumerate(labels)], startangle=90, counterclock=False, wedgeprops={"width": 0.42, "edgecolor": "white"})
        tot = sum(vals)
        ax.legend(w, [f"{_trim(l)}  {v / tot * 100:.1f}%" for l, v in zip(labels, vals)], loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=9, frameon=False)
        ax.set_title(P["title"], fontsize=11, loc="left", color=INK, fontweight="bold")
    if kind == "bars":
        labels = [_trim(str(l)) for l in P["labels"]]
        series = P["series"]
        stacked = R.chart == "stacked_bar"
        horizontal = P.get("horizontal") and len(series) == 1 and not stacked
        n = len(series)
        idx = list(range(len(labels)))
        if horizontal:
            idx = idx[::-1]
            vals = series[0]["values"]
            cols = list(palette.category_colours(range(len(vals))).values())
            bars = ax.barh(idx, vals, color=cols if len(vals) > 1 else ACCENT, height=0.65)
            ax.set_yticks(idx)
            ax.set_yticklabels(labels)
            sh = P.get("shares")
            for i, (b, v) in enumerate(zip(bars, vals)):
                ax.text(b.get_width(), b.get_y() + b.get_height() / 2, " " + fmt(v) + (f"  ({sh[i]:.1f}%)" if sh else ""), va="center", fontsize=9, color=INK)
            ax.margins(x=0.22)
            ax.xaxis.set_major_formatter(lambda v, _: _short(v))
        else:
            width = 0.8 / (1 if stacked else n)
            bottom = [0.0] * len(labels)
            for k, s in enumerate(series):
                vals = [v or 0 for v in s["values"]]
                if stacked:
                    ax.bar(idx, vals, 0.7, bottom=bottom, label=_trim(s["name"]), color=SERIES[k % len(SERIES)])
                    bottom = [b + v for b, v in zip(bottom, vals)]
                else:
                    pos = [i - 0.4 + width * (k + 0.5) for i in idx]
                    bars = ax.bar(pos, vals, width * 0.92, label=_trim(s["name"]), color=SERIES[k % len(SERIES)] if n > 1 else ACCENT)
                    if len(labels) * n <= 14:
                        for b, v in zip(bars, vals):
                            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), fmt(v), ha="center", va="bottom", fontsize=8.5, color=INK)
            ax.set_xticks(idx)
            ax.set_xticklabels(labels, rotation=0 if len(labels) <= 6 else 40, ha="center" if len(labels) <= 6 else "right")
            ax.yaxis.set_major_formatter(lambda v, _: _short(v))
            if n > 1:
                ax.legend(fontsize=9, frameon=False, ncol=min(n, 4))
        _style(ax, P["title"])
        ax.grid(axis="x" if horizontal else "y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    elif kind == "series":
        x = list(range(len(P["x"])))
        stacked = R.chart == "stacked_bar"
        if R.chart == "bar" or stacked:
            bottom = [0.0] * len(x)
            n = len(P["series"])
            width = 0.8 / (1 if stacked else n)
            for k, s in enumerate(P["series"]):
                vals = [v or 0 for v in s["values"]]
                if stacked:
                    ax.bar(x, vals, 0.8, bottom=bottom, label=_trim(s["name"]), color=SERIES[k % len(SERIES)])
                    bottom = [b + v for b, v in zip(bottom, vals)]
                else:
                    ax.bar([i - 0.4 + width * (k + 0.5) for i in x], vals, width * 0.92, label=_trim(s["name"]), color=SERIES[k % len(SERIES)] if n > 1 else ACCENT)
        else:
            for k, s in enumerate(P["series"]):
                ax.plot(x, [float("nan") if v is None else v for v in s["values"]], color=SERIES[k % len(SERIES)], linewidth=2, marker="o" if len(x) <= 40 else None, markersize=3.5, label=_trim(s["name"]))
            if len(P["series"]) == 1 and len(x) <= 24:
                vals = P["series"][0]["values"]
                for i in (0, len(x) - 1, max(range(len(vals)), key=lambda j: -1e30 if vals[j] is None else vals[j])):
                    if vals[i] is not None:
                        ax.annotate(fmt(vals[i]), (x[i], vals[i]), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=8.5, color=INK)
        for i in P.get("marks") or []:
            v = P["series"][0]["values"][i]
            ax.scatter([x[i]], [v], s=110, facecolors="none", edgecolors=MARK, linewidths=2, zorder=5)
            ax.annotate(P["x"][i], (x[i], v), textcoords="offset points", xytext=(0, 11), ha="center", fontsize=8.5, color=MARK, fontweight="bold")
        step = max(1, len(x) // 10)
        ax.set_xticks(x[::step])
        ax.set_xticklabels(P["x"][::step], rotation=40 if len(x) > 6 else 0, ha="right" if len(x) > 6 else "center")
        ax.yaxis.set_major_formatter(lambda v, _: _short(v))
        if len(P["series"]) > 1:
            ax.legend(fontsize=9, frameon=False, ncol=min(len(P["series"]), 4))
        _style(ax, P["title"])
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    elif kind == "hist":
        e, c = P["edges"], P["counts"]
        ax.bar([(e[i] + e[i + 1]) / 2 for i in range(len(c))], c, width=[(e[i + 1] - e[i]) * 0.95 for i in range(len(c))], color=ACCENT)
        ax.set_xlabel(P["xlabel"], fontsize=8)
        ax.set_ylabel("Rows", fontsize=8)
        ax.xaxis.set_major_formatter(lambda v, _: _short(v))
        _style(ax, P["title"])
    elif kind == "scatter":
        ax.scatter(P["x"], P["y"], s=9, color=ACCENT, alpha=0.45, linewidths=0)
        ax.set_xlabel(P["xlabel"], fontsize=8)
        ax.set_ylabel(P["ylabel"], fontsize=8)
        ax.xaxis.set_major_formatter(lambda v, _: _short(v))
        ax.yaxis.set_major_formatter(lambda v, _: _short(v))
        _style(ax, P["title"])
    chartfinish.finish(fig)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=CHART_DPI, facecolor=PANEL)
    return True


def chart_file(workdir: Path, q: dict, chart: str) -> Path:
    key = hashlib.sha1((qmod.signature(q) + "|" + chart).encode()).hexdigest()[:14]
    return workdir / "charts" / f"lens_{key}.png"
