"""Deterministic analysis toolbox. Agents choose tools + parameters; the numbers always come from here.

Each tool returns a Finding dict:
  {title, text, stats, chart, significant, action, tool}
so the LLM never has to invent a statistic."""
from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib
import matplotlib.ticker  # noqa: F401

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from . import derived, wording
from .measures import measure_kind
from .config import CHART_DPI

# The first colour is the app accent (the highlight); other bars are a neutral warm grey, so the highlight is the one colour that stands out.
PALETTE = ["#A4461F", "#22A699", "#F2994A", "#E5484D", "#8E6BEE", "#3FA9F5", "#A3A847", "#C45AB3"]
plt.rcParams.update({
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "axes.grid.axis": "y", "grid.alpha": 0.25,
    "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "figure.dpi": 100,
    "axes.prop_cycle": matplotlib.cycler(color=PALETTE),
    # fallbacks so non-English column names (Hindi, Tamil, Chinese, Arabic...) render wherever the system has such a font
    "font.family": "sans-serif",
    "font.sans-serif": ["Inter", "DejaVu Sans", "Noto Sans", "Noto Sans Devanagari", "Nirmala UI", "Segoe UI", "Arial Unicode MS", "Noto Sans CJK SC", "Arial"],
})


def additive(name: str, ctx: dict | None = None) -> bool:
    """Totals make sense for this column (so a trend or group table sums it); ratios, rates and prices are averaged instead.
    The run's brief kinds (ctx['kinds']) win over the word lists."""
    return measure_kind(name, overrides=(ctx or {}).get("kinds")) == "additive"

# ---------------------------------------------------------------- catalog (shown to the planner LLM)
CATALOG = {
    "distribution": {"params": {"col": "numeric column"}, "doc": "Histogram + skew of one numeric column."},
    "outliers": {"params": {"col": "numeric column"}, "doc": "IQR outlier check with box plot."},
    "category_share": {"params": {"col": "categorical column"}, "doc": "Frequency of the top categories."},
    "group_compare": {"params": {"target": "numeric or binary column", "group": "categorical column"},
                      "doc": "Compare the target across groups with a significance test and effect size."},
    "outcome_compare": {"params": {"target": "binary column (e.g. churn yes/no)", "col": "numeric column"},
                        "doc": "Does a numeric driver differ between rows with and without a binary outcome? (Mann-Whitney)"},
    "robust_compare": {"params": {"target": "numeric column", "group": "categorical column"},
                       "doc": "Median-based, outlier-resistant version of group_compare (Kruskal-Wallis)."},
    "pareto": {"params": {"target": "non-negative numeric column", "group": "categorical column"},
               "doc": "Which groups contribute most of the total (80/20 view)."},
    "trend": {"params": {"target": "numeric or binary column", "date": "date column"},
              "doc": "Target over time with a fitted trend line."},
    "correlation": {"params": {}, "doc": "Correlation heatmap across the relevant numeric columns."},
    "heatmap": {"params": {"target": "numeric or binary column", "group_a": "categorical column", "group_b": "a different categorical column"},
                "doc": "Average (or rate) of the target for every combination of two categorical columns; shows segments that only stand out in combination."},
    "segment_trend": {"params": {"target": "numeric or binary column", "date": "date column", "group": "categorical column"},
                      "doc": "Target over time for each of the top groups, with a corrected trend test per group (who is growing, who is declining)."},
    "scatter": {"params": {"x": "numeric column", "y": "a different numeric column"}, "doc": "Scatter plot of two numeric columns with a fitted line and a rank-correlation test."},
    "composition": {"params": {"group": "categorical column", "by": "a different categorical column"},
                    "doc": "100% stacked bars: how the mix of one category changes across another, with a chi-square test."},
    "seasonality": {"params": {"target": "numeric or binary column", "date": "date column"}, "doc": "Weekday and month-of-year pattern of the target, tested."},
    "summary_table": {"params": {}, "doc": "Table of count/missing/mean/median/std/min/max for numeric columns (descriptive only)."},
    "group_table": {"params": {"target": "numeric or binary column", "group": "categorical column"}, "doc": "Table of rows, average/rate, median, total and share per group."},
}

# ---------------------------------------------------------------- helpers
def _f(x):
    try:
        v = float(x)
        return None if np.isnan(v) or np.isinf(v) else round(v, 6)
    except (TypeError, ValueError):
        return None


def _fmt(x) -> str:
    x = float(x)
    if abs(x) >= 1e6:
        return f"{x / 1e6:.2f}M"
    if abs(x) >= 1e4:
        return f"{x / 1e3:.1f}K"
    if abs(x) >= 100:
        return f"{x:,.0f}"
    return f"{x:.3g}"


def _cur(col, ctx) -> str:
    """Currency symbol (from the optional business context) for money-like columns, else ''."""
    sym = (ctx or {}).get("currency") or ""
    return sym if sym and col in ((ctx or {}).get("money") or ()) and _derived(ctx, col) is None else ""     # a derived ratio is a % or a plain number, never an amount


def _bars(ax):
    """The BarContainer of an axes (with error bars, containers[0] is the ErrorbarContainer, which cannot be labelled)."""
    from matplotlib.container import BarContainer
    return next(c for c in ax.containers if isinstance(c, BarContainer))


def _title(ax, text: str, width: int = 62) -> None:
    """Takeaway-style chart title: left-aligned and wrapped, so the headline is readable without the surrounding text."""
    import textwrap
    ax.set_title("", loc="center")  # drop any default centred title so two titles never overlap
    ax.set_title(textwrap.fill(text, width), fontsize=11, fontweight="bold", loc="left")


def _dates(ax) -> None:
    """Readable, non-overlapping date ticks."""
    import matplotlib.dates as mdates
    loc = mdates.AutoDateLocator(minticks=3, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))


def _pf(p) -> str:
    """p-value for text: '<0.001' for tiny values (never the misleading '=0'), else '=0.0123'."""
    return "<0.001" if p < 0.001 else f"={p:.3g}"


def _slug(*parts) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", "_".join(str(p) for p in parts)).strip("_").lower()[:70]


def _save(fig, ctx, name: str) -> str:
    charts = Path(ctx["charts_dir"])
    charts.mkdir(parents=True, exist_ok=True)
    fig.savefig(charts / f"{name}.png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f"charts/{name}.png"


def finding(tool, title, text, stats_=None, chart=None, significant=None, action=None) -> dict:
    return {"tool": tool, "title": title, "text": text, "stats": stats_ or {}, "chart": chart,
            "significant": significant, "action": action}


def to_binary(s: pd.Series):
    """Returns (0/1 series, positive_label) or (None, None) if the column is not binary."""
    vals = list(pd.unique(s.dropna()))
    if len(vals) != 2:
        return None, None
    pos_words = {"yes", "y", "true", "t", "1", "1.0", "churn", "churned", "positive", "success", "converted", "fraud", "default"}
    pos = next((v for v in vals if str(v).strip().lower() in pos_words), None)
    if pos is None:
        pos = sorted(vals, key=str)[-1]
    return (s == pos).astype(float).where(s.notna()), pos


def target_values(s: pd.Series):
    b, pos = to_binary(s)
    if b is not None:
        return b, "binary", pos
    return pd.to_numeric(s, errors="coerce"), "numeric", None


def effect_label(eta2: float) -> str:
    return "negligible" if eta2 < 0.01 else "small" if eta2 < 0.06 else "medium" if eta2 < 0.14 else "large"


def assoc_strength(df: pd.DataFrame, target: str, group: str) -> float:
    """Cheap 0..1 association (eta^2 or Cramer's V) used by the planner to rank candidate groups."""
    try:
        y, kind, _ = target_values(df[target])
        d = pd.DataFrame({"g": df[group].astype(str), "y": y}).dropna()
        if d["g"].nunique() < 2 or len(d) < 10:
            return 0.0
        if kind == "binary":
            ct = pd.crosstab(d["g"], d["y"])
            if min(ct.shape) < 2:
                return 0.0
            chi2 = stats.chi2_contingency(ct)[0]
            return float(np.sqrt(chi2 / (len(d) * (min(ct.shape) - 1))))
        grand = d["y"].mean()
        ssb = sum(len(g) * (g["y"].mean() - grand) ** 2 for _, g in d.groupby("g"))
        sst = ((d["y"] - grand) ** 2).sum()
        return float(ssb / sst) if sst > 0 else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------- display limits (statistics always use ALL the data; only the picture is trimmed)
TOP_BARS = 8        # ranking bars
TOP_SHARE = 7       # share charts (parts of a whole)
TOP_LINES = 5       # per-group trend lines
TOP_CELLS = 6       # heatmap rows and columns
TABLE_ROWS = 10     # table rows shown before "Show all"
MONTH_CYCLE_MIN_DAYS = 700   # under two years of dates a month-of-year pattern cannot be told apart from a trend
TABLE_CAP = 500     # most rows a table finding carries
LONG_LABEL = 12     # labels longer than this switch ranking bars to horizontal
OTHER_COLOR = "#C9CED8"


def pick_shown(order: list, must: list, n: int) -> list:
    """The first `n` of `order` (already ranked), keeping every item of `must` (the groups the headline names) by swapping out the weakest others."""
    if len(order) <= n:
        return list(order)
    shown = list(order[:n])
    for m in must:
        if m in order and m not in shown:
            for i in range(len(shown) - 1, -1, -1):
                if shown[i] not in must:
                    shown[i] = m
                    break
    return sorted(shown, key=order.index)


def other_label(k: int) -> str:
    return f"Other ({k} group{'s' if k != 1 else ''})"


def trim_note(shown: int, total: int, noun: str = "groups") -> str:
    return f"showing top {shown} of {total} {noun}; statistics use all {total}" if total > shown else ""


def _derived(ctx, col):
    """The derived-metric spec ({a, b, ...}) when `col` is a ratio the planner created; its groups are summed first and divided after."""
    return ((ctx or {}).get("derived") or {}).get(col)


def _pct_ratio(df, spec) -> bool:
    """A ratio whose numerator can go negative (a profit-like measure, judged from the data of the spec's numerator) is shown as a percentage with loss wording."""
    if spec is None or spec.get("a") not in df.columns:
        return False
    return bool((pd.to_numeric(df[spec["a"]], errors="coerce") < 0).any())


def _rfmt(v, pct: bool, plain) -> str:
    return f"{v:.1%}" if pct else plain(v)


def _loss_note(v, name, pct: bool) -> str:
    return f", loss-making (negative {name})" if pct and v < 0 else ""


def _shorten(s, n: int = 28) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def _hbars(labels, values, texts, colors, xlabel="", note="", err=None, percent=False):
    """Horizontal ranking bars: label on the left, the value and n written beside each bar, so no tick label is ever crammed or rotated."""
    k = len(labels)
    fig, ax = plt.subplots(figsize=(7.2, max(2.6, 0.42 * k + 1.5)))
    y = np.arange(k)
    ev = [0.0 if e is None or not np.isfinite(e) else float(e) for e in (err if err is not None else [0.0] * k)]
    ax.barh(y, values, color=colors, height=0.68, xerr=ev if any(ev) else None, error_kw=dict(alpha=0.5, capsize=2))
    ax.set_yticks(y, [_shorten(l) for l in labels])
    ax.invert_yaxis()
    hi, lo = max(max(v + e for v, e in zip(values, ev)), 0.0), min(min(values), 0.0)
    span = (hi - lo) or 1.0
    for yi, v, e, t in zip(y, values, ev, texts):
        ax.text(max(v + e, 0.0) + 0.012 * span, yi, t, va="center", ha="left", fontsize=8)
    ax.set_xlim(lo - (0.03 * span if lo < 0 else 0), hi + 0.5 * span)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", alpha=0.25)
    if percent:
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_xlabel(xlabel + (f"\n{note}" if note else ""), fontsize=9)
    ax.tick_params(axis="y", length=0)
    return fig, ax


def _vbars(labels, values, texts, colors, xlabel="", ylabel="", note="", err=None, percent=False):
    """Vertical bars for a few groups with short names; the value and n sit above each bar, never under the axis."""
    import textwrap
    k = len(labels)
    fig, ax = plt.subplots(figsize=(max(5.2, 1.15 * k + 2.4), 3.9))
    ev = [0.0 if e is None or not np.isfinite(e) else float(e) for e in (err if err is not None else [0.0] * k)]
    ax.bar(range(k), values, color=colors, width=0.62, yerr=ev if any(ev) else None, capsize=3, error_kw=dict(alpha=0.5))
    ax.set_xticks(range(k), [textwrap.fill(str(l), 12) for l in labels])
    hi = max(max(v + e for v, e in zip(values, ev)), 0.0)
    lo = min(min(values), 0.0)
    span = (hi - lo) or 1.0
    for i, (v, e, t) in enumerate(zip(values, ev, texts)):
        ax.text(i, max(v + e, 0.0) + 0.02 * span, t, ha="center", va="bottom", fontsize=8)
    ax.set_ylim(lo - (0.03 * span if lo < 0 else 0), hi + 0.3 * span)
    ax.grid(axis="x", visible=False)
    if percent:
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_ylabel(ylabel)
    ax.set_xlabel(xlabel + (f"\n{note}" if note else ""), fontsize=9)
    return fig, ax


def ranked_bars(labels, values, ns, fmt, other_at=None, xlabel="", ylabel="", note="", err=None, percent=False):
    """Pick the layout from the data: horizontal when there are more than 6 bars or long labels. The best bar is dark, others light, 'Other' grey."""
    texts = [f"{fmt(v)}  (n={int(n):,})" for v, n in zip(values, ns)]
    colors = [PALETTE[0] if i == 0 else "#CFC7B9" for i in range(len(labels))]
    if other_at is not None:
        colors[other_at] = OTHER_COLOR
    if len(labels) > 6 or max((len(str(l)) for l in labels), default=0) > LONG_LABEL:
        return _hbars(labels, values, texts, colors, xlabel, note, err, percent)
    return _vbars(labels, values, [t.replace("  (", "\n(") for t in texts], colors, xlabel, ylabel, note, err, percent)


# ---------------------------------------------------------------- tools
def distribution(df, ctx, col):
    """Histogram. All values feed the statistics; the picture is cut at the 99th percentile (and says how many values are beyond it), or drawn on a log axis when the skew is extreme."""
    x = pd.to_numeric(df[col], errors="coerce").dropna()
    if len(x) < 5:
        return None
    skew = float(x.skew())
    q1, q3, q99 = (float(v) for v in x.quantile([0.25, 0.75, 0.99]))
    median = float(x.median())
    log_axis = bool(skew > 5 and x.min() > 0 and q99 > 20 * median)           # extreme right skew: a log axis shows every value
    beyond = int((x > q99).sum())
    clipped = bool(not log_axis and beyond > 0 and x.max() > q99 + 0.1 * (q99 - x.min()))
    shown = x[x <= q99] if clipped else x
    nb = min(40, max(8, int(np.sqrt(len(x)))))
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    if log_axis:
        ax.hist(shown, bins=np.logspace(np.log10(shown.min()), np.log10(shown.max()), nb + 1), color=PALETTE[0], alpha=0.85)
        ax.set_xscale("log")
    else:
        ax.hist(shown, bins=nb, color=PALETTE[0], alpha=0.85)
    ax.axvline(median, color=PALETTE[3], ls="--", lw=1.5, label=f"median {_cur(col, ctx)}{_fmt(median)}")
    ax.axvline(x.mean(), color=PALETTE[1], ls=":", lw=1.8, label=f"mean {_cur(col, ctx)}{_fmt(x.mean())}")
    if clipped:
        ax.set_xlim(float(shown.min()), q99)
    _title(ax, f"{col}: half of all rows fall between {_cur(col, ctx)}{_fmt(q1)} and {_cur(col, ctx)}{_fmt(q3)}")
    note = ("log scale: a few very large values would otherwise hide the rest" if log_axis
            else f"{beyond:,} value{'s' if beyond != 1 else ''} above {_cur(col, ctx)}{_fmt(q99)} (the 99th percentile) not shown; all values are in the statistics" if clipped else "")
    ax.set(xlabel=col + (f"\n{note}" if note else ""), ylabel="Count")
    ax.legend(frameon=False)
    shape = "right-skewed (a few very large values)" if skew > 1 else "left-skewed" if skew < -1 else "roughly symmetric"
    text = (f"**{col}** has a median of {_fmt(median)} and mean of {_fmt(x.mean())} (range {_fmt(x.min())} to {_fmt(x.max())}); "
            f"the distribution is {shape}." + (" Medians are a safer summary than means here." if abs(skew) > 1 else "")
            + (f" The chart is cut at the 99th percentile ({beyond:,} larger value{'s' if beyond != 1 else ''} not shown); the statistics use every value." if clipped else "")
            + (" The chart uses a log scale because of the extreme skew." if log_axis else ""))
    return finding("distribution", f"Distribution of {col}", text,
                   {"n": int(len(x)), "mean": _f(x.mean()), "median": _f(median), "std": _f(x.std()), "skew": _f(skew), "q1": _f(q1), "q3": _f(q3),
                    "p99": _f(q99), "beyond_p99": beyond if clipped else 0, "clipped": clipped, "log_axis": log_axis},
                   _save(fig, ctx, _slug("dist", col)))


def outliers(df, ctx, col):
    x = pd.to_numeric(df[col], errors="coerce").dropna()
    if len(x) < 8:
        return None
    q1, q3 = x.quantile([0.25, 0.75])
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    n_out = int(((x < lo) | (x > hi)).sum())
    fig, ax = plt.subplots(figsize=(6.5, 2.4))
    kw = dict(widths=0.6, patch_artist=True, boxprops=dict(facecolor=PALETTE[0], alpha=0.35),
              medianprops=dict(color=PALETTE[3]), flierprops=dict(marker="o", markersize=3, alpha=0.4))
    try:
        ax.boxplot(x, orientation="horizontal", **kw)  # matplotlib >= 3.10
    except TypeError:
        ax.boxplot(x, vert=False, **kw)
    ax.set(xlabel=col, yticks=[])
    _title(ax, f"{col}: {n_out} of {len(x)} values sit outside the usual range")
    pct = n_out / len(x)
    text = f"**{col}**: {n_out} of {len(x)} values ({pct:.1%}) fall outside the usual range ({_fmt(lo)} to {_fmt(hi)})."
    action = f"Check whether the extreme values of {col} are errors or genuinely important cases before drawing conclusions." if pct > 0.05 else None
    return finding("outliers", f"Outliers in {col}", text, {"n": int(len(x)), "outliers": n_out, "outlier_pct": _f(pct)},
                   _save(fig, ctx, _slug("out", col)), action=action)


def category_share(df, ctx, col):
    """How often each value occurs: all values if there are at most 7, else the top 7 plus one 'Other' bar."""
    vc = df[col].astype(str).value_counts()
    if len(vc) < 2:
        return None
    total_groups, rows = int(len(vc)), int(vc.sum())
    top = vc if total_groups <= TOP_SHARE else vc.head(TOP_SHARE)
    rest = vc.iloc[len(top):]
    labels, values = [str(k) for k in top.index], [int(v) for v in top.values]
    if len(rest):
        labels.append(other_label(len(rest)))
        values.append(int(rest.sum()))
    fig, ax = _hbars(labels, values, [f"{v:,} ({v / rows:.0%})" for v in values], [PALETTE[1]] * len(top) + ([OTHER_COLOR] if len(rest) else []),
                     "Rows", trim_note(len(top), total_groups, "values"))
    _title(ax, f"{col}: which values appear most often" + (f" (top {len(top)} of {total_groups})" if len(rest) else ""))
    share = top.iloc[0] / rows
    text = (f"**{col}** has {total_groups} distinct values; the most common is '{top.index[0]}' ({share:.0%} of records)."
            + (f" The chart shows the top {len(top)} and pools the other {len(rest)} as Other." if len(rest) else ""))
    return finding("category_share", f"Composition of {col}", text,
                   {"distinct": total_groups, "top": {str(k): int(v) for k, v in top.head(5).items()}, "shown": [str(k) for k in top.index], "shown_n": int(len(top)),
                    "other": {"groups": int(len(rest)), "rows": int(rest.sum())} if len(rest) else None},
                   _save(fig, ctx, _slug("share", col)))


def _group_frame(df, target, group):
    """Every group, however many: the statistics must see all the data. Only the picture is trimmed (see ranked_bars)."""
    y, kind, pos = target_values(df[target])
    d = pd.DataFrame({"g": df[group].astype(str), "y": y}).dropna()
    return d, kind, pos


def group_compare(df, ctx, target, group, within=None, within_value=None):
    """`within` + `within_value` limit the comparison to the rows of one parent value (a drill-down into one category)."""
    if within is not None:
        df = df[df[within].astype(str) == str(within_value)]
        scope = f" within {within} = {within_value}"
    else:
        scope = ""
    d, kind, pos = _group_frame(df, target, group)
    if d["g"].nunique() < 2 or len(d) < 10:
        return None
    n_groups = d["g"].nunique()
    cur = _cur(target, ctx)
    spec = _derived(ctx, target) if kind == "numeric" else None      # a derived ratio is shown as a ratio of sums, never a mean of row ratios
    pct = _pct_ratio(df, spec)
    if kind == "binary":
        agg = d.groupby("g")["y"].agg(["mean", "count"])
        ct = pd.crosstab(d["g"], d["y"])
        if min(ct.shape) < 2:
            return None
        chi2, p, _, _ = stats.chi2_contingency(ct)
        v = float(np.sqrt(chi2 / (len(d) * (min(ct.shape) - 1))))
        effect, effect_name = v, "cramers_v"
    else:
        agg = d.groupby("g")["y"].agg(["mean", "median", "count", "sem"])
        groups = [g["y"].values for _, g in d.groupby("g") if len(g) >= 2]
        if len(groups) < 2:
            return None
        p = float(stats.f_oneway(*groups)[1])
        grand = d["y"].mean()
        ssb = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
        sst = float(((d["y"] - grand) ** 2).sum())
        effect, effect_name = (float(ssb / sst) if sst > 0 else 0.0), "eta_squared"
        if spec is not None:
            p, effect = derived.ratio_permutation_test(df.loc[d.index], spec, d["g"])      # the test judges the quantity the chart shows: sum A / sum B per group
            agg["mean"] = derived.group_ratio(df.loc[d.index], spec, d["g"])
            agg = agg.dropna(subset=["mean"])
            if len(agg) < 2:
                return None
    agg = agg.sort_values("mean", ascending=False)
    order = list(agg.index)
    top, bot = order[0], order[-1]
    means = [float(m) for m in agg["mean"]]
    label = f"'{pos}' rate" if kind == "binary" else "overall value" if spec is not None else "average"
    sig_word = "statistically significant" if p < 0.05 else "not statistically significant"
    test = f"chi-square p{_pf(p)}, Cramér's V={effect:.2f}, {effect_label(effect * effect)} effect" if kind == "binary" else f"permutation test on the ratio of sums p{_pf(p)}, eta²={effect:.3f}, {effect_label(effect)} effect" if spec is not None else f"ANOVA p{_pf(p)}, eta²={effect:.3f}, {effect_label(effect)} effect"
    if kind == "binary":
        text = (f"The {label} for **{target}** is highest in **{group} = {top}** ({means[0]:.1%}) and lowest in **{bot}** ({means[-1]:.1%}); across all rows it is {float(d['y'].mean()):.1%}. The difference is {sig_word} ({test}).")
    else:
        what = f"**{target}** (total {spec['a']} over total {spec['b']})" if spec is not None else f"Average **{target}**"
        text = f"{what} is highest for **{group} = {top}** ({_rfmt(means[0], pct, lambda v: cur + _fmt(v))}{_loss_note(means[0], target, pct)}) and lowest for **{bot}** ({_rfmt(means[-1], pct, lambda v: cur + _fmt(v))}{_loss_note(means[-1], target, pct)}). The difference is {sig_word} ({test})."
    st = {"kind": kind, "p": _f(p), "effect": _f(effect), "effect_name": effect_name, "n": int(len(d)), "groups": int(n_groups),
          "min_group_n": int(agg["count"].min()), "best": str(top), "worst": str(bot), "values": {str(k): _f(r) for k, r in agg["mean"].items()}}
    if kind == "binary":
        st["positive"] = str(pos)
        st["overall_rate"] = _f(d["y"].mean())
    else:
        st["target_skew"] = _f(d["y"].skew())
        st["ratio_of_sums"] = spec is not None
        if spec is None and additive(target, ctx) and (d["y"] >= 0).all() and d["y"].sum() > 0:      # each shown group's share of the total, for the "so what" of an average comparison
            sums = d.groupby("g")["y"].sum()
            st["shares"] = {str(k): _f(float(sums[k] / d["y"].sum())) for k in (top, bot)}
    if within is not None:
        text = f"Within {within} = {within_value}: {text}"
        st["within"] = {"column": within, "value": str(within_value)}

    # ---- what is drawn: the top 8 by the plotted value, always with the best and worst group, then one pooled 'Other' bar
    shown = pick_shown(order, [top, bot], TOP_BARS)
    rest = [g for g in order if g not in shown]
    fmtv = (lambda v: f"{v:.0%}") if kind == "binary" else (lambda v: f"{v:.1%}") if pct else (lambda v: f"{cur}{_fmt(v)}")
    labels, values, ns = [str(g) for g in shown], [float(agg.loc[g, "mean"]) for g in shown], [int(agg.loc[g, "count"]) for g in shown]
    err = [1.96 * float(agg.loc[g, "sem"]) if (kind != "binary" and spec is None and pd.notna(agg.loc[g, "sem"])) else 0.0 for g in shown]
    other_at = None
    if rest:
        sub = d[d["g"].isin(rest)]
        other_value = derived.ratio_of_sums(df.loc[sub.index], spec) if spec is not None else float(sub["y"].mean())
        if other_value is not None:
            labels.append(other_label(len(rest)))
            values.append(float(other_value))
            ns.append(int(len(sub)))
            err.append(0.0)
            other_at = len(labels) - 1
            st["other"] = {"groups": len(rest), "value": _f(other_value), "n": int(len(sub))}
    note = trim_note(len(shown), int(n_groups), "groups")
    st.update({"shown": [str(g) for g in shown], "shown_n": len(shown), "total_groups": int(n_groups)})
    ylab = f"Rate of {target} = {pos}" if kind == "binary" else f"{'Overall' if spec is not None else 'Mean'} {target}"
    fig, ax = ranked_bars(labels, values, ns, fmtv, other_at, xlabel=group if len(labels) <= 6 else ylab, ylabel=ylab if len(labels) <= 6 else "", note=note,
                          err=err, percent=(kind == "binary"))
    ratio = means[0] / means[-1] if kind != "binary" and means[-1] > 0 and means[0] > 0 else None
    if kind == "binary":
        headline = f"{top} has the highest {target} rate ({means[0]:.0%}) vs {means[-1]:.0%} for {bot}"
    elif ratio and ratio >= 1.15:
        headline = f"{top} averages {ratio:.1f}× the {target} of {bot}"
    else:
        headline = f"Average {target} by {group}"
    _title(ax, headline + ("" if p < 0.05 else " (difference not significant)"))
    if note:
        text += f" The chart shows the top {len(shown)} of {n_groups} groups" + (f" and pools the other {len(rest)} as Other" if rest else "") + "; the statistics use all groups."
    # Statistical significance is not practical significance: only recommend action on a medium-or-larger effect.
    meaningful = p < 0.05 and (effect >= 0.2 if kind == "binary" else effect >= 0.06)
    action = None
    if meaningful:
        # Neutral wording: for a binary outcome (churn, default, ...) "highest" can be the bad end, so don't call it "strongest".
        what = f"the highest rate of {target} = {pos}" if kind == "binary" else f"the highest {target}"
        action = (f"**{group} = {top}** has {what}, versus **{bot}** at the lowest; investigate what separates them. "
                  f"This is an association, so test any change (e.g. a targeted pilot) before assuming cause.")
    return finding("group_compare", f"{target} by {group}{scope}", text, st, _save(fig, ctx, _slug("cmp", target, group, within, within_value)),
                   significant=bool(p < 0.05), action=action)


def outcome_compare(df, ctx, target, col):
    """Binary outcome (e.g. churn yes/no) vs a numeric driver (e.g. tenure): Mann-Whitney U + rank-biserial effect."""
    y, kind, pos = target_values(df[target])
    if kind != "binary":
        return None
    d = pd.DataFrame({"x": pd.to_numeric(df[col], errors="coerce"), "y": y}).dropna()
    a, b = d.loc[d["y"] == 1, "x"], d.loc[d["y"] == 0, "x"]
    if len(a) < 5 or len(b) < 5:
        return None
    u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    r = float(2 * u / (len(a) * len(b)) - 1)  # rank-biserial: >0 means the outcome group tends to have larger values
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    names = {("tick_labels" if _has_tick_labels() else "labels"): [f"{target} = {pos}", "Other"]}  # kwarg renamed in matplotlib 3.9
    ax.boxplot([a, b], showfliers=False, patch_artist=True, boxprops=dict(facecolor=PALETTE[0], alpha=0.35),
               medianprops=dict(color=PALETTE[3]), **names)
    ax.set(ylabel=col)
    _title(ax, f"{col} when {target} = {pos} vs all other rows")
    direction = "higher" if r > 0 else "lower"
    text = (f"Rows with **{target} = {pos}** have a {direction} median **{col}** ({_fmt(a.median())} vs {_fmt(b.median())}). "
            f"The difference is {'statistically significant' if p < 0.05 else 'not statistically significant'} (Mann-Whitney p{_pf(p)}, rank-biserial effect={abs(r):.2f}, "
            f"{'small' if abs(r) < 0.3 else 'medium' if abs(r) < 0.5 else 'large'}).")
    action = (f"{col} looks like an early-warning signal for {target} = {pos} ({direction} values go with the outcome); consider monitoring it, then test an intervention before assuming cause."
              if p < 0.05 and abs(r) >= 0.2 else None)
    return finding("outcome_compare", f"{col} vs {target}", text,
                   {"kind": "outcome", "p": _f(p), "effect": _f(abs(r)), "effect_name": "rank_biserial", "n": int(len(d)),
                    "min_group_n": int(min(len(a), len(b))), "median_outcome": _f(a.median()), "median_other": _f(b.median()),
                    "positive": str(pos), "direction": direction},
                   _save(fig, ctx, _slug("oc", target, col)), significant=bool(p < 0.05), action=action)


def _has_tick_labels() -> bool:
    import inspect
    return "tick_labels" in inspect.signature(plt.Axes.boxplot).parameters


def robust_compare(df, ctx, target, group):
    d, kind, _ = _group_frame(df, target, group)
    if kind == "binary":
        return None
    groups = [g["y"].values for _, g in d.groupby("g") if len(g) >= 2]
    if len(groups) < 2:
        return None
    p = float(stats.kruskal(*groups)[1])
    med = d.groupby("g")["y"].agg(["median", "count"]).sort_values("median", ascending=False)
    order = list(med.index)
    shown = pick_shown(order, [order[0], order[-1]], TOP_BARS)
    fig, ax = plt.subplots(figsize=(7.2, max(2.8, 0.42 * len(shown) + 1.5)))
    kw = dict(showfliers=False, patch_artist=True, boxprops=dict(facecolor=PALETTE[0], alpha=0.35), medianprops=dict(color=PALETTE[3]))
    data = [d.loc[d["g"] == g, "y"].values for g in shown]
    try:
        ax.boxplot(data, orientation="horizontal", **kw)  # matplotlib >= 3.10
    except TypeError:
        ax.boxplot(data, vert=False, **kw)
    ax.set_yticks(range(1, len(shown) + 1), [_shorten(g) for g in shown])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    note = trim_note(len(shown), len(order), "groups")
    ax.set_xlabel(target + (f"\n{note}" if note else ""), fontsize=9)
    _title(ax, f"Median {target} across {group} (outlier-resistant check)")
    text = (f"Robustness check for **{target}** by **{group}** using medians: highest median in **{med.index[0]}** ({_fmt(med['median'].iloc[0])}), "
            f"lowest in **{med.index[-1]}** ({_fmt(med['median'].iloc[-1])}). Kruskal-Wallis p{_pf(p)} - "
            f"{'the group difference holds up without relying on means' if p < 0.05 else 'the difference does NOT hold up with a rank-based test'}.")
    return finding("robust_compare", f"Robustness check: {target} by {group}", text,
                   {"p": _f(p), "n": int(len(d)), "kind": "robust", "target": target, "group": group, "shown_n": len(shown), "total_groups": len(order)},
                   _save(fig, ctx, _slug("rob", target, group)), significant=bool(p < 0.05))


def pareto(df, ctx, target, group):
    y = pd.to_numeric(df[target], errors="coerce")
    d = pd.DataFrame({"g": df[group].astype(str), "y": y}).dropna()
    if d.empty or (d["y"] < 0).any():
        return None
    g = d.groupby("g")["y"].sum().sort_values(ascending=False)
    if len(g) < 5 or g.sum() == 0:  # with fewer than 5 groups "top 3 share" is meaningless
        return None
    share = g / g.sum()
    cum = share.cumsum()
    k = min(3, len(g))
    top_share = float(share.head(k).sum())
    n_shown = len(g) if len(g) <= TOP_SHARE else TOP_SHARE        # a share chart: the top 7 plus one pooled 'Other' bar
    rest = g.iloc[n_shown:]
    labels, values = [str(i) for i in g.index[:n_shown]], [float(v) for v in g.values[:n_shown]]
    texts = [f"{share.iloc[i]:.0%}  (cum. {cum.iloc[i]:.0%})" for i in range(n_shown)]
    colors = [PALETTE[1] if i < k else "#BFE3DE" for i in range(n_shown)]
    if len(rest):
        labels.append(other_label(len(rest)))
        values.append(float(rest.sum()))
        texts.append(f"{rest.sum() / g.sum():.0%}  (cum. 100%)")
        colors.append(OTHER_COLOR)
    fig, ax = _hbars(labels, values, texts, colors, f"Total {_cur(target, ctx)}{target}".strip(), trim_note(n_shown, len(g), "groups"))
    _title(ax, f"The top {k} {group} values drive {top_share:.0%} of total {target}")
    text = f"The top {k} values of **{group}** account for **{top_share:.0%}** of total {target} (leader: {g.index[0]}, {share.iloc[0]:.0%})."
    action = (f"{target} is concentrated in a few {group} values ({top_share:.0%} from the top {k}); protect them and look for growth in the long tail."
              if top_share > 0.5 and len(g) >= 5 else None)
    return finding("pareto", f"Where {target} comes from ({group})", text,
                   {"top_k": k, "top_share": _f(top_share), "groups": int(len(g)), "n": int(len(d)), "shown_n": n_shown,
                    "other": {"groups": int(len(rest)), "share": _f(rest.sum() / g.sum())} if len(rest) else None},
                   _save(fig, ctx, _slug("par", target, group)), action=action)


def trend(df, ctx, target, date):
    y, kind, _ = target_values(df[target])
    d = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "y": y}).dropna().set_index("t").sort_index()
    if len(d) < 10:
        return None
    span = (d.index.max() - d.index.min()).days
    rule = "D" if span <= 45 else "W" if span <= 180 else "MS"
    spec = _derived(ctx, target) if kind == "numeric" else None
    agg = "ratio of sums" if spec is not None else "sum" if (kind == "numeric" and additive(target, ctx)) else "mean"
    if spec is not None:        # per period: total of the first column over total of the second, never a mean of row ratios
        parts = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "x": pd.to_numeric(df[spec["a"]], errors="coerce"),
                              "y": pd.to_numeric(df[spec["b"]], errors="coerce")}).dropna().set_index("t").sort_index().resample(rule).sum()
        series = (parts["x"] / parts["y"].where(parts["y"] != 0)).dropna()
    else:
        series = d["y"].resample(rule).agg(agg)
        if agg == "mean":
            series = series.dropna()
    counts = d["y"].resample(rule).count()
    full = series.reindex(counts.index).where(counts > 0)         # a period with no rows is a gap, not a zero
    series = full.dropna()
    if len(series) < 4:
        return None
    x = np.flatnonzero(full.notna().values)
    lr = stats.linregress(x, series.values)
    base = abs(series.mean()) or 1.0
    change = lr.slope * (x[-1] - x[0]) / base
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.plot(full.index, full.values, color=PALETTE[0], marker="o", ms=3, lw=1.8)
    ax.plot(series.index, lr.intercept + lr.slope * x, color=PALETTE[3], ls="--", lw=1.4, label="trend")
    unit = {"D": "day", "W": "week", "MS": "month"}[rule]
    cur = _cur(target, ctx)
    ax.annotate(f"best {cur}{_fmt(series.max())}", (series.idxmax(), series.max()), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=8, color=PALETTE[1])
    ax.annotate(f"weakest {cur}{_fmt(series.min())}", (series.idxmin(), series.min()), textcoords="offset points", xytext=(0, -15), ha="center", fontsize=8, color=PALETTE[3])
    ax.margins(y=0.2)
    _dates(ax)
    ax.set_ylabel(f"{agg.capitalize()} {target} per {unit}")
    ax.legend(frameon=False)
    direction = "upward" if lr.slope > 0 else "downward"
    sig = lr.pvalue < 0.05
    fit_end = float(lr.intercept + lr.slope * x[-1])
    steep_fall = lr.slope < 0 and wording.steep({"relative_change": change, "end": fit_end})       # a fall of 100% or more of the level, or to below zero: no percentage
    size = f"falling steeply, from {_fmt(series.iloc[0])} to {_fmt(series.iloc[-1])}" if steep_fall else f"{change:+.0%}"
    _title(ax, f"{target} is trending {'up' if lr.slope > 0 else 'down'} ({size} over the period)" if sig else f"No clear trend in {target} over time (p={lr.pvalue:.2f})")
    gaps = full.index[full.isna()]
    no_data = f" No data in {len(gaps)} {unit}{'s' if len(gaps) != 1 else ''} (for example {gaps[0].date()}); these are left out of the best and weakest {unit}." if len(gaps) else ""
    text = (f"**{target}** ({agg} per {unit}) shows a {'statistically significant ' if sig else 'weak, non-significant '}{direction} trend "
            f"({size if steep_fall else 'about ' + size} across the period, p{_pf(lr.pvalue)}). Best {unit}: {series.idxmax().date()} ({cur}{_fmt(series.max())}); weakest: {series.idxmin().date()} ({cur}{_fmt(series.min())}).{no_data} "
            f"The first and last {unit}s may be partial.")
    action = (f"{target} is trending {direction} over time; find out what changed around the weakest {unit} ({series.idxmin().date()})." if sig else None)
    return finding("trend", f"{target} over time", text,
                   {"p": _f(lr.pvalue), "slope": _f(lr.slope), "relative_change": _f(change), "periods": int(len(series)), "empty_periods": int(len(gaps)), "freq": rule, "agg": agg, "n": int(len(d)),
                    "first": _f(series.iloc[0]), "last": _f(series.iloc[-1]), "fit_end": _f(fit_end)},
                   _save(fig, ctx, _slug("trend", target, date)), significant=bool(sig), action=action) | {
        "series": [[str(i.date()), _f(v)] for i, v in series.items()],           # the period totals (or averages), for the report card; kept out of `stats`, which the AI number check reads
        "date_coverage": _f(float(pd.to_datetime(df[date], errors="coerce").notna().mean()))}


def correlation(df, ctx, cols=None):
    cols = [c for c in (cols or []) if c in df.columns]
    num = df[cols].apply(pd.to_numeric, errors="coerce") if cols else df.select_dtypes("number")
    num = num.loc[:, num.nunique() > 2].iloc[:, :12]
    if num.shape[1] < 2 or len(num) < 10:
        return None
    corr = num.corr()
    fig, ax = plt.subplots(figsize=(6.2, 5.2))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(corr)), corr.columns, rotation=40, ha="right")
    ax.set_yticks(range(len(corr)), corr.columns)
    ax.grid(False)
    for i in range(len(corr)):
        for j in range(len(corr)):
            ax.text(j, i, f"{corr.values[i, j]:.2f}", ha="center", va="center", fontsize=8,
                    color="white" if abs(corr.values[i, j]) > 0.6 else "black")
    fig.colorbar(im, ax=ax, fraction=0.046)
    _title(ax, "Which numeric columns move together (closer to +1 or -1 = stronger link)")
    pairs = (corr.where(np.triu(np.ones(corr.shape), 1).astype(bool)).stack().sort_values(key=abs, ascending=False).head(3))
    listing = "; ".join(f"{a} & {b}: r={v:.2f}" for (a, b), v in pairs.items())
    strong = [(a, b, v) for (a, b), v in pairs.items() if abs(v) >= 0.5]
    ratios = [c for c in corr.columns if c in (ctx.get("derived") or {})]
    basis = f" {', '.join(ratios)} is a ratio, so these correlations use its per-row values (not a group-level ratio of sums)." if ratios else ""
    text = f"Strongest relationships: {listing}. Correlation shows columns move together, not that one causes the other.{basis}"
    action = f"{strong[0][0]} and {strong[0][1]} are strongly related (r={strong[0][2]:.2f}); consider whether one can serve as a lever or an early indicator for the other." if strong else None
    return finding("correlation", "Relationships between numeric columns", text,
                   {"top_pairs": {f"{a}|{b}": _f(v) for (a, b), v in pairs.items()}, "n": int(len(num))},
                   _save(fig, ctx, "correlation"), action=action)


def heatmap(df, ctx, target, group_a, group_b):
    """Target by every combination of two categorical columns. Cells with fewer than 10 rows are blanked, not guessed.
    The best and weakest cells are found over ALL cells; only the top 6 x 6 by size are drawn (always including the rows and columns of those two cells).
    Cell-level gaps are NOT individually tested, so this tool is exploratory (significant=None)."""
    if group_a == group_b:
        return None
    y, kind, pos = target_values(df[target])
    d = pd.DataFrame({"a": df[group_a].astype(str), "b": df[group_b].astype(str), "y": y}).dropna()
    if len(d) < 30:
        return None
    spec = _derived(ctx, target) if kind == "numeric" else None
    if spec is not None:        # a cell is its own ratio of sums, never a mean of row ratios
        parts = pd.DataFrame({"a": d["a"], "b": d["b"], "x": pd.to_numeric(df.loc[d.index, spec["a"]], errors="coerce"), "w": pd.to_numeric(df.loc[d.index, spec["b"]], errors="coerce")}).dropna()
        sums = parts.pivot_table(index="a", columns="b", values=["x", "w"], aggfunc="sum")
        piv = sums["x"] / sums["w"].where(sums["w"] != 0)
    else:
        piv = d.pivot_table(index="a", columns="b", values="y", aggfunc="mean")
    cnt = d.pivot_table(index="a", columns="b", values="y", aggfunc="count").reindex(index=piv.index, columns=piv.columns).fillna(0)
    valid = piv.where(cnt >= 10)
    cells = valid.stack()
    if valid.shape[0] < 2 or valid.shape[1] < 2 or int(cells.size) < 4:
        return None
    (a_hi, b_hi), (a_lo, b_lo) = cells.idxmax(), cells.idxmin()
    hi, lo = float(cells.max()), float(cells.min())
    rows_by_size, cols_by_size = list(d["a"].value_counts().index), list(d["b"].value_counts().index)
    rs, cs = pick_shown(rows_by_size, [a_hi, a_lo], TOP_CELLS), pick_shown(cols_by_size, [b_hi, b_lo], TOP_CELLS)
    shown = valid.loc[rs, cs]
    shown = shown.loc[shown.mean(axis=1).sort_values(ascending=False).index, shown.mean(axis=0).sort_values(ascending=False).index]
    cnt = cnt.loc[shown.index, shown.columns]
    cur = _cur(target, ctx)
    pct = _pct_ratio(df, spec)
    fmt = (lambda v: f"{v:.0%}") if kind == "binary" else (lambda v: f"{v:.1%}") if pct else (lambda v: f"{cur}{_fmt(v)}")
    trimmed = len(rows_by_size) > len(rs) or len(cols_by_size) > len(cs)
    note = (f"showing the {len(rs)} biggest of {len(rows_by_size)} {group_a} values and {len(cs)} of {len(cols_by_size)} {group_b} values; best and weakest cells use all data"
            if trimmed else "")
    fig, ax = plt.subplots(figsize=(max(5.6, 1.05 * shown.shape[1] + 3.2), max(3.4, 0.6 * shown.shape[0] + 2.2)))
    cmap = matplotlib.colormaps["Blues"].with_extremes(bad="#EEF0F4")
    ax.imshow(np.ma.masked_invalid(shown.values.astype(float)), cmap=cmap, aspect="auto")
    ax.set_xticks(range(shown.shape[1]), [_shorten(c, 16) for c in shown.columns], rotation=30 if shown.shape[1] > 4 else 0, ha="right" if shown.shape[1] > 4 else "center")
    ax.set_yticks(range(shown.shape[0]), [_shorten(r, 20) for r in shown.index])
    ax.set(xlabel=group_b + (f"\n{note}" if note else ""), ylabel=group_a)
    ax.grid(False)
    span = (hi - lo) or 1.0
    for i in range(shown.shape[0]):
        for j in range(shown.shape[1]):
            v = shown.values[i, j]
            if pd.isna(v):
                ax.text(j, i, "n<10", ha="center", va="center", fontsize=7, color="#9AA3B2")
            else:
                ax.text(j, i, f"{fmt(v)}\nn={int(cnt.values[i, j])}", ha="center", va="center", fontsize=8,
                        color="white" if (v - lo) / span > 0.6 else "#1F2937")
    _title(ax, f"Best: {a_hi} × {b_hi} ({fmt(hi)}). Weakest: {a_lo} × {b_lo} ({fmt(lo)})")
    gap = f", a {hi / lo:.1f}× gap" if kind != "binary" and lo > 0 else ""
    text = (f"Across **{group_a}** and **{group_b}**, {'the rate of ' + str(pos) + ' for ' if kind == 'binary' else ''}**{target}** is highest for **{a_hi} × {b_hi}** ({fmt(hi)}{_loss_note(hi, target, pct)}) "
            f"and lowest for **{a_lo} × {b_lo}** ({fmt(lo)}{_loss_note(lo, target, pct)}){gap}. Only combinations with at least 10 rows are shown; these cell-level gaps are not individually "
            f"tested, so treat them as leads to check rather than proven differences." + (f" The chart draws only the {len(rs)} biggest {group_a} and {len(cs)} biggest {group_b} values (of {len(rows_by_size)} and {len(cols_by_size)}); the best and weakest cells are found over all data." if trimmed else ""))
    return finding("heatmap", f"{target}: {group_a} × {group_b}", text,
                   {"kind": "heatmap", "n": int(len(d)), "cells": int(cells.size), "positive": str(pos) if kind == "binary" else None, "target_kind": kind,
                    "top_cell": [str(a_hi), str(b_hi)], "top_value": _f(hi), "bottom_cell": [str(a_lo), str(b_lo)], "bottom_value": _f(lo),
                    "shown_rows": [str(r) for r in shown.index], "shown_cols": [str(c) for c in shown.columns],
                    "total_rows": int(len(rows_by_size)), "total_cols": int(len(cols_by_size))},
                   _save(fig, ctx, _slug("heat", target, group_a, group_b)), significant=None)


def segment_trend(df, ctx, target, date, group):
    """Target over time for each group. Every group with enough data is tested (p-values Bonferroni-corrected across all of them);
    only the 5 biggest are drawn, always including the fastest and slowest."""
    y, kind, _ = target_values(df[target])
    spec = _derived(ctx, target) if kind == "numeric" else None
    d = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "g": df[group].astype(str), "y": y}).dropna()
    if len(d) < 40:
        return None
    sizes = d["g"].value_counts()
    d = d[d["g"].isin(sizes[sizes >= 20].index)]
    if d["g"].nunique() < 2:
        return None
    span = (d["t"].max() - d["t"].min()).days
    rule = "D" if span <= 45 else "W" if span <= 180 else "MS"
    use_sum = kind == "numeric" and additive(target, ctx)
    if spec is not None:
        parts = pd.DataFrame({"x": pd.to_numeric(df.loc[d.index, spec["a"]], errors="coerce"), "y": pd.to_numeric(df.loc[d.index, spec["b"]], errors="coerce")})
    series = {}
    for g, sub in d.groupby("g"):
        if spec is not None:        # ratio of sums per period, never a mean of row ratios
            ps = parts.loc[sub.index].dropna()
            ps.index = sub.loc[ps.index, "t"]
            sums = ps.sort_index().resample(rule).sum()
            s = (sums["x"] / sums["y"].where(sums["y"] != 0)).dropna()
        else:
            s = sub.set_index("t").sort_index()["y"].resample(rule).agg("sum" if use_sum else "mean")
            if not use_sum:
                s = s.dropna()
        if len(s) >= 6:
            series[g] = s
    if len(series) < 2:
        return None
    res = {}
    for g, s in series.items():
        lr = stats.linregress(np.arange(len(s)), s.values)
        start, end, mean = float(lr.intercept), float(lr.intercept + lr.slope * (len(s) - 1)), float(abs(s.mean()))
        res[g] = {"p": float(lr.pvalue), "relative_change": float(lr.slope * (len(s) - 1) / (mean or 1.0)), "periods": int(len(s)),
                  "start": start, "end": end, "change_abs": end - start, "first": float(s.iloc[0]), "last": float(s.iloc[-1]),
                  "pct_ok": bool(start > 0 and end > 0 and abs(start) >= wording.NEAR_ZERO * mean)}     # near zero or a sign flip: a percentage would mislead, so the absolute change is shown
    k = len(res)
    for g in res:
        res[g]["p_adjusted"] = min(1.0, res[g]["p"] * k)
    fast = max(res, key=lambda g: res[g]["relative_change"])
    slow = min(res, key=lambda g: res[g]["relative_change"])
    sig_groups = [g for g in res if res[g]["p_adjusted"] < 0.05]
    survives = bool(sig_groups)
    unit = {"D": "day", "W": "week", "MS": "month"}[rule]
    by_size = [g for g in sizes.index if g in res]
    shown = pick_shown(by_size, [fast, slow], TOP_LINES)
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    for i, g in enumerate(sorted(shown, key=lambda g: -res[g]["relative_change"])):
        s = series[g]
        short = f"{res[g]['relative_change']:+.0%}" if wording.pct_ok(res[g]) else f"{res[g]['change_abs']:+,.3g}"
        ax.plot(s.index, s.values, marker="o", ms=2.5, lw=2.0 if g in (fast, slow) else 1.2, color=PALETTE[i % len(PALETTE)], label=f"{_shorten(g, 22)} ({short})")
    ax.legend(frameon=False, fontsize=8, title=f"{group} (change over period)", title_fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5))
    _dates(ax)
    ax.set_ylabel(f"{'Ratio of sums' if spec is not None else 'Sum' if use_sum else 'Mean'} {target} per {unit}")
    note = trim_note(len(shown), k, "groups")
    if note:
        ax.set_xlabel(note, fontsize=9)
    st_view = {"groups": res, "fastest": str(fast), "slowest": str(slow), "survives_correction": survives}
    headline = wording.trend_headline(st_view, group, target, _fmt)
    _title(ax, headline)
    ranked = sorted(res, key=lambda g: -res[g]["relative_change"])
    listed = ranked if k <= 8 else [g for g in ranked if g in (fast, slow)] + [g for g in sig_groups if g not in (fast, slow)][:4]
    desc = "; ".join(f"**{g}** {wording.change_phrase(res[g], _fmt)} ({'significant' if res[g]['p_adjusted'] < 0.05 else 'not significant'})" for g in listed)
    shape = wording.trend_shape(st_view)
    lead_in = {"mixed": "The groups move in different directions. ", "up": "All groups move up. ", "down": "All groups move down. "}[shape]
    text = (lead_in + f"Change in **{target}** over the period by **{group}** ({unit}ly, linear trend): {desc}" + (f"; {k - len(listed)} more groups not listed" if len(listed) < k else "") + ". "
            f"P-values are corrected for {k} groups. The first and last {unit}s may be partial." + (f" The chart draws the {len(shown)} biggest groups plus the fastest and slowest (statistics use all {k})." if note else ""))
    action = (f"**{fast}** and **{slow}** are moving in different directions for {target}; find out what is driving each before reallocating effort." if survives and shape == "mixed" else None)
    return finding("segment_trend", f"{target} over time by {group}", text,
                   {"kind": "segment_trend", "groups": {g: {kk: (vv if isinstance(vv, bool) else _f(vv)) for kk, vv in v.items()} for g, v in res.items()}, "shape": shape, "fastest": str(fast), "slowest": str(slow),
                    "p": _f(min(v["p"] for v in res.values())), "p_adjusted": _f(min(v["p_adjusted"] for v in res.values())), "survives_correction": survives,
                    "n": int(len(d)), "freq": rule, "periods": int(min(v["periods"] for v in res.values())), "shown": [str(g) for g in shown], "shown_n": len(shown), "total_groups": k},
                   _save(fig, ctx, _slug("segtrend", target, date, group)), significant=survives, action=action)


# ---------------------------------------------------------------- scatter, composition, seasonality, tables
def scatter(df, ctx, x, y):
    """Two numeric columns against each other: Spearman rank correlation (robust to outliers) plus a fitted line."""
    if x == y:
        return None
    a, b = pd.to_numeric(df[x], errors="coerce"), pd.to_numeric(df[y], errors="coerce")
    d = pd.DataFrame({"x": a, "y": b}).dropna()
    if len(d) < 30 or d["x"].nunique() < 5 or d["y"].nunique() < 5:
        return None
    rho, p = stats.spearmanr(d["x"], d["y"])
    if np.isnan(rho):
        return None
    lr = stats.linregress(d["x"], d["y"])
    shown = d.sample(min(len(d), 2500), random_state=0)
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    ax.scatter(shown["x"], shown["y"], s=12, alpha=0.35, color=PALETTE[0], edgecolor="none")
    xs = np.linspace(d["x"].min(), d["x"].max(), 50)
    ax.plot(xs, lr.intercept + lr.slope * xs, color=PALETTE[3], lw=1.8)
    ax.set(xlabel=x, ylabel=y)
    ax.grid(axis="x", alpha=0.2)
    hidden = 0
    for axis_name, ser in (("y", d["y"]), ("x", d["x"])):   # a few extreme points can squash everything else: zoom to the bulk and say so
        q1, q99 = ser.quantile(0.01), ser.quantile(0.99)
        span_ = q99 - q1
        if span_ > 0 and (ser.max() > q99 + 1.5 * span_ or ser.min() < q1 - 1.5 * span_):
            lo, hi = q1 - 0.1 * span_, q99 + 0.1 * span_
            hidden += int(((ser < lo) | (ser > hi)).sum())
            (ax.set_ylim if axis_name == "y" else ax.set_xlim)(lo, hi)
    if hidden:
        ax.text(0.99, 0.01, f"{hidden} extreme point{'s' if hidden > 1 else ''} outside the view", transform=ax.transAxes, ha="right", va="bottom", fontsize=7, color="#888")
    sig = bool(p < 0.05)
    word = "positive" if rho > 0 else "negative"
    strength = "strong" if abs(rho) >= 0.6 else "moderate" if abs(rho) >= 0.3 else "weak"
    _title(ax, f"{y} rises with {x} (rho={rho:+.2f})" if sig and rho > 0 and abs(rho) >= 0.3 else f"{y} falls as {x} rises (rho={rho:+.2f})" if sig and rho < 0 and abs(rho) >= 0.3
           else f"{x} and {y} are only weakly related (rho={rho:+.2f})")
    text = (f"**{x}** and **{y}** have a {strength} {word} relationship (Spearman rho={rho:+.2f}, p{_pf(p)}, n={len(d):,}). "
            f"{'It is statistically significant' if sig else 'It is not statistically significant'}; a straight-line fit suggests {y} changes by about {_fmt(lr.slope)} for each unit of {x}. "
            f"This shows association, not cause and effect.")
    action = (f"{x} and {y} move together ({strength}); find out whether {x} is something you can change before treating it as a lever." if sig and abs(rho) >= 0.3 else None)
    return finding("scatter", f"{y} vs {x}", text,
                   {"p": _f(p), "rho": _f(rho), "effect": _f(abs(rho)), "effect_name": "spearman_rho", "slope": _f(lr.slope), "n": int(len(d)), "x": x, "y": y},
                   _save(fig, ctx, _slug("sc", x, y)), significant=sig, action=action)


def composition(df, ctx, group, by):
    """How the mix of one category changes across another (100% stacked bars) with a chi-square test and Cramer's V.
    The test uses every row; the picture shows the 7 biggest values of each column plus one pooled 'Other'."""
    if group == by:
        return None
    d = pd.DataFrame({"g": df[group].astype(str), "b": df[by].astype(str)})
    d = d[~d["g"].isin(["nan", "None", ""]) & ~d["b"].isin(["nan", "None", ""])]
    ct = pd.crosstab(d["g"], d["b"])
    if ct.shape[0] < 2 or ct.shape[1] < 2 or len(d) < 30:
        return None
    chi2, p, _, _ = stats.chi2_contingency(ct)
    v = float(np.sqrt(chi2 / (len(d) * (min(ct.shape) - 1))))
    share = ct.div(ct.sum(axis=1), axis=0)
    overall = ct.sum(axis=0) / ct.values.sum()
    dev = share - overall
    gi, bi = np.unravel_index(np.abs(dev.values).argmax(), dev.shape)
    big_g, big_b, big_diff = dev.index[gi], dev.columns[bi], float(dev.values[gi, bi])
    rows_by_size, cols_by_size = list(ct.sum(axis=1).sort_values(ascending=False).index), list(ct.sum(axis=0).sort_values(ascending=False).index)
    rs, cs = pick_shown(rows_by_size, [big_g], TOP_SHARE), pick_shown(cols_by_size, [big_b], TOP_SHARE)
    rest_r, rest_c = [r for r in rows_by_size if r not in rs], [c for c in cols_by_size if c not in cs]
    view = ct.loc[rs, cs].copy()
    if rest_c:
        view[other_label(len(rest_c))] = ct.loc[rs, rest_c].sum(axis=1)
    if rest_r:
        view.loc[other_label(len(rest_r))] = ct.loc[rest_r, cs].sum(axis=0).tolist() + ([int(ct.loc[rest_r, rest_c].values.sum())] if rest_c else [])
    shown_share = view.div(view.sum(axis=1), axis=0)
    fig, ax = plt.subplots(figsize=(7.4, 0.5 * len(shown_share) + 2.4))
    left = np.zeros(len(shown_share))
    ys = [_shorten(i, 24) for i in shown_share.index]
    for i, col in enumerate(shown_share.columns):
        other = str(col).startswith("Other (")
        ax.barh(ys, shown_share[col].values, left=left, color=OTHER_COLOR if other else PALETTE[i % len(PALETTE)], label=_shorten(col, 22), edgecolor="white", linewidth=0.6)
        for j, val in enumerate(shown_share[col].values):
            if val >= 0.08:
                ax.text(left[j] + val / 2, j, f"{val:.0%}", ha="center", va="center", fontsize=7.5, color="#1F2937" if other else "white")
        left += shown_share[col].values
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.invert_yaxis()
    ax.grid(False)
    ax.legend(title=by, frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=8, title_fontsize=8)
    notes = [n for n in (f"{group}: top {len(rs)} of {len(rows_by_size)}" if rest_r else "", f"{by}: top {len(cs)} of {len(cols_by_size)}" if rest_c else "") if n]
    note = "showing " + "; ".join(notes) + "; the test uses all rows" if notes else ""
    if note:
        ax.set_xlabel(note, fontsize=9)
    sig = bool(p < 0.05)
    _title(ax, f"The mix of {by} differs across {group}" if sig else f"The mix of {by} is similar across {group}")
    text = (f"The mix of **{by}** {'differs' if sig else 'does not differ reliably'} across **{group}** (chi-square p{_pf(p)}, Cramér's V={v:.2f}, n={len(d):,}). "
            f"Biggest departure from the overall mix: **{big_g}** has {abs(big_diff):.0%} {'more' if big_diff > 0 else 'less'} {big_b} than average."
            + (f" The chart shows {'; '.join(notes)}, with the rest pooled as Other; the test uses every row." if notes else ""))
    return finding("composition", f"{by} mix across {group}", text,
                   {"p": _f(p), "effect": _f(v), "effect_name": "cramers_v", "n": int(len(d)), "min_group_n": int(ct.sum(axis=1).min()),
                    "group": group, "by": by, "biggest": [str(big_g), str(big_b), _f(big_diff)],
                    "shown_rows": [str(r) for r in rs], "shown_cols": [str(c) for c in cs], "total_rows": len(rows_by_size), "total_cols": len(cols_by_size)},
                   _save(fig, ctx, _slug("comp", group, by)), significant=sig,
                   action=(f"The {by} mix depends on {group}; tailor offers or channels by {group} rather than using one approach for all." if sig and v >= 0.2 else None))


def _cycle_obs(d: pd.DataFrame, name: str, total: bool) -> pd.DataFrame | None:
    """One observation per row (rate-type measures) or per period total (additive measures): a calendar month's total in one year, a day's total.
    Month totals need at least two full years so each calendar month is seen more than once; the partial first/last month is dropped."""
    if not total:
        return pd.DataFrame({"key": d["t"].dt.dayofweek if name == "weekday" else d["t"].dt.month, "y": d["y"]})
    if name == "weekday":
        day = d.groupby(d["t"].dt.normalize())["y"].sum()
        return pd.DataFrame({"key": day.index.dayofweek, "y": day.values})
    first, last = d["t"].min(), d["t"].max()
    mt = d.groupby([d["t"].dt.year.rename("yr"), d["t"].dt.month.rename("key")])["y"].sum().reset_index()
    if first.day > 3:
        mt = mt[~((mt["yr"] == first.year) & (mt["key"] == first.month))]
    if last.day < last.days_in_month - 3:
        mt = mt[~((mt["yr"] == last.year) & (mt["key"] == last.month))]
    return mt[["key", "y"]] if mt["yr"].nunique() >= 2 else None


def _ratio_obs(d: pd.DataFrame, name: str) -> pd.DataFrame | None:
    """One observation per period for a derived ratio: that day's (or calendar month's) total A over total B."""
    per = d["t"].dt.normalize() if name == "weekday" else d["t"].dt.to_period("M")
    s = d.groupby(per)[["x", "w"]].sum()
    s = s[s["w"] != 0]
    if s.empty:
        return None
    key = s.index.dayofweek if name == "weekday" else s.index.month
    return pd.DataFrame({"key": np.asarray(key), "y": (s["x"] / s["w"]).values})


def seasonality(df, ctx, target, date):
    """Does the target follow a weekday or month-of-year pattern? Tests each available cycle and corrects for testing two.
    Additive measures (amounts, counts, profit) are tested on period totals (month totals averaged across years, day totals by weekday), because a per-row average
    says nothing about how much is sold in a month; rate-type measures (percentages, ratios, binary) keep the per-row average."""
    y, kind, _ = target_values(df[target])
    d = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "y": y}).dropna()
    if len(d) < 60:
        return None
    spec = _derived(ctx, target) if kind == "numeric" else None
    if spec is not None:
        d["x"], d["w"] = pd.to_numeric(df.loc[d.index, spec["a"]], errors="coerce"), pd.to_numeric(df.loc[d.index, spec["b"]], errors="coerce")
        d = d.dropna()
    total = kind == "numeric" and _derived(ctx, target) is None and additive(target, ctx) and bool((d["y"] >= 0).all())
    span = (d["t"].max() - d["t"].min()).days
    cycles = {}
    if span >= 56 and d["t"].dt.dayofweek.nunique() >= 5:
        cycles["weekday"] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if span >= MONTH_CYCLE_MIN_DAYS and d["t"].dt.month.nunique() >= 10:
        cycles["month"] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if not cycles:
        return None
    results = {}
    for name, labels in cycles.items():
        obs = _ratio_obs(d, name) if spec is not None else _cycle_obs(d, name, total)
        if obs is None:
            continue
        grp = obs.groupby("key")["y"]
        need = 2 if ((total or spec is not None) and name == "month") else 5
        groups = [g.values for _, g in grp if len(g) >= need]
        if len(groups) < 3 or (name == "month" and len(groups) < 10):
            continue
        # period totals: a handful of values per calendar month, near-normal because they are sums, so ANOVA (a rank test has almost no power at 2-3 per group)
        p = float((stats.f_oneway(*groups)[1] if (total or spec is not None) else stats.kruskal(*groups)[1])) * len(cycles)
        if not np.isfinite(p):
            continue
        means = grp.mean()
        grand = obs["y"].mean()
        shown_grand = grand
        if spec is not None:        # shown values: total A over total B within each weekday / month, not an average of daily or row ratios
            key = d["t"].dt.dayofweek if name == "weekday" else d["t"].dt.month
            s = d.assign(key=key).groupby("key")[["x", "w"]].sum()
            means = (s["x"] / s["w"].where(s["w"] != 0)).dropna()
            shown_grand = float(d["x"].sum() / d["w"].sum()) if d["w"].sum() else grand
        ssb = sum(len(g) * (g.mean() - grand) ** 2 for _, g in grp)
        sst = ((obs["y"] - grand) ** 2).sum()
        results[name] = {"p": min(1.0, p), "means": means, "labels": labels, "eta": float(ssb / sst) if sst > 0 else 0.0, "min_n": int(grp.size().min()), "grand": float(shown_grand), "n": int(len(obs))}
    if not results:
        return None
    name = min(results, key=lambda k: results[k]["p"])
    r = results[name]
    fig, axes = plt.subplots(1, len(results), figsize=(5.2 * len(results), 3.5), squeeze=False)
    for ax, (nm, rr) in zip(axes[0], results.items()):
        m = rr["means"]
        names = [rr["labels"][int(i) - (1 if nm == "month" else 0)] for i in m.index]
        bars = ax.bar(names, m.values, color=[PALETTE[1] if v == m.max() else PALETTE[3] if v == m.min() else "#C9D3F8" for v in m.values])
        ax.bar_label(bars, labels=[(f"{v:.0%}" if kind == "binary" else f"{v:.1%}" if _pct_ratio(df, spec) else _fmt(v)) for v in m.values], padding=2, fontsize=7)
        ax.axhline(rr["grand"], color="#888", ls="--", lw=1)
        ax.margins(y=0.15)
        ax.grid(axis="x", visible=False)
        ax.set_ylabel("Rate of " + target if kind == "binary" else (f"Average {'monthly' if nm == 'month' else 'daily'} total of " if total else "Average ") + target)
        _title(ax, f"{target} by {'day of week' if nm == 'weekday' else 'month of year'}", 40)
    sig = bool(r["p"] < 0.05)
    m = r["means"]
    hi, lo = r["labels"][int(m.idxmax()) - (1 if name == "month" else 0)], r["labels"][int(m.idxmin()) - (1 if name == "month" else 0)]
    fig.suptitle((f"{target} peaks on {hi} and is lowest on {lo}" if name == "weekday" else f"{target} peaks in {hi} and is lowest in {lo}") if sig else f"No reliable {name} pattern in {target}",
                 fontsize=12, fontweight="bold", x=0.02, ha="left", y=1.04)
    pct = _pct_ratio(df, spec)
    fmt = (lambda v: f"{v:.0%}") if kind == "binary" else (lambda v: f"{v:.1%}") if pct else _fmt
    unit = "day of the week" if name == "weekday" else "month of the year"
    text = (f"**{target}** {'varies reliably' if sig else 'does not vary reliably'} by {unit} ({'ANOVA on period totals' if total else 'Kruskal-Wallis'} p{_pf(r['p'])}, effect size eta²={r['eta']:.3f}). "
            f"Highest: **{hi}** ({fmt(m.max())}{_loss_note(m.max(), target, pct)}); lowest: **{lo}** ({fmt(m.min())}{_loss_note(m.min(), target, pct)}); overall {fmt(r['grand'])}. "
            + (f"Values are the average {'monthly' if name == 'month' else 'daily'} total{' across years' if name == 'month' else ''}, not a per-row average." if total
               else f"Values are total {spec['a']} over total {spec['b']} for each {'day of the week' if name == 'weekday' else 'month'}; the test compares {'daily' if name == 'weekday' else 'monthly'} ratios." if spec is not None
               else "Values are averages per row."))
    for on, orr in results.items():     # the cycle that was not the most reliable is still reported (month-of-year is a required view), in one sentence
        if on == name:
            continue
        om, off = orr["means"], 1 if on == "month" else 0
        ohi, olo = orr["labels"][int(om.idxmax()) - off], orr["labels"][int(om.idxmin()) - off]
        ounit = "day of the week" if on == "weekday" else "month of the year"
        text += (f" By {ounit}, **{target}** {'varies reliably' if orr['p'] < 0.05 else 'does not vary reliably'} (p{_pf(orr['p'])}, eta²={orr['eta']:.3f}): "
                 f"highest **{ohi}** ({fmt(om.max())}), lowest **{olo}** ({fmt(om.min())}).")
    action = (f"{target} follows a {name} pattern; plan staffing, stock or promotions around {hi} (high) and {lo} (low)." if sig and r["eta"] >= 0.01 else None)
    vals = {r["labels"][int(i) - (1 if name == "month" else 0)]: _f(v) for i, v in m.items()}
    return finding("seasonality", f"{target} by {unit}", text,
                   {"p": _f(r["p"]), "effect": _f(r["eta"]), "effect_name": "eta_squared", "n": r["n"], "cycle": name, "cycles": {k: {"p": _f(v["p"]), "best": v["labels"][int(v["means"].idxmax()) - (1 if k == "month" else 0)], "worst": v["labels"][int(v["means"].idxmin()) - (1 if k == "month" else 0)]} for k, v in results.items()}, "best": hi, "worst": lo,
                    "values": vals, "min_group_n": r["min_n"], "kind": kind, "basis": "period totals" if total else "ratio of sums" if spec is not None else "row averages"},
                   _save(fig, ctx, _slug("seas", target, date)), significant=sig, action=action)


def summary_table(df, ctx, cols=None):
    """A plain descriptive table of the numeric columns: no statistics claimed. A derived ratio shows its ratio of sums as the 'Mean'."""
    cols = [c for c in (cols or []) if c in df.columns]
    if not cols:
        cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    cols = cols[:12]
    rows = []
    ratios = []
    for c in cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() == 0:
            continue
        spec = _derived(ctx, c)
        mean = derived.ratio_of_sums(df, spec) if spec is not None else s.mean()
        if spec is not None:
            ratios.append(c)
        rows.append([c, int(s.notna().sum()), f"{s.isna().mean():.0%}", _fmt(mean) if mean is not None else "-", _fmt(s.median()), _fmt(s.std()), _fmt(s.min()), _fmt(s.max())])
    if not rows:
        return None
    text = f"Descriptive summary of {len(rows)} numeric column{'s' if len(rows) != 1 else ''} (count, share missing, mean, median, standard deviation, minimum and maximum)."
    if ratios:
        text += f" For {', '.join(ratios)} the mean is the ratio of sums (total of the first column over total of the second), not an average of row ratios."
    return finding("summary_table", "Summary of numeric columns", text, {"n": int(len(df)), "columns": len(rows)},
                   None, significant=None) | {"table": {"columns": ["Column", "Count", "Missing", "Mean", "Median", "Std dev", "Min", "Max"], "rows": rows}}


def group_table(df, ctx, target, group):
    """Per-group table of the target: rows, average (or rate), median, total and share of total. Every group is in the table; the page shows 10 and offers 'Show all'."""
    y, kind, _ = target_values(df[target])
    d = pd.DataFrame({"g": df[group].astype(str), "y": y}).dropna()
    d = d[~d["g"].isin(["nan", "None", ""])]
    if d["g"].nunique() < 2 or len(d) < 10:
        return None
    spec = _derived(ctx, target) if kind == "numeric" else None
    use_sum = kind == "numeric" and spec is None and additive(target, ctx) and (d["y"] >= 0).all()
    g = d.groupby("g")["y"].agg(["count", "mean", "median", "sum"])
    if spec is not None:
        g["mean"] = derived.group_ratio(df.loc[d.index], spec, d["g"])
        g = g.dropna(subset=["mean"])
    g = g.sort_values("sum" if use_sum else "mean", ascending=False)
    total_groups = len(g)
    g = g.head(TABLE_CAP)
    cur = _cur(target, ctx)
    cols = ["Group", "Rows", "Rate" if kind == "binary" else "Overall (ratio of sums)" if spec is not None else "Average"] + ([] if kind == "binary" else ["Median"]) + (["Total", "Share of total"] if use_sum else [])
    rows = []
    for name, r in g.iterrows():
        row = [name, int(r["count"]), f"{r['mean']:.1%}" if kind == "binary" else f"{cur}{_fmt(r['mean'])}"]
        if kind != "binary":
            row.append(f"{cur}{_fmt(r['median'])}")
        if use_sum:
            row += [f"{cur}{_fmt(r['sum'])}", f"{r['sum'] / d['y'].sum():.0%}"]
        rows.append(row)
    text = f"{target} for each {group} value" + (" ranked by total." if use_sum else " ranked by " + ("rate." if kind == "binary" else "overall value." if spec is not None else "average.")) + " Groups with few rows give unstable averages, so look at the Rows column first."
    return finding("group_table", f"{target} by {group} (table)", text, {"n": int(len(d)), "groups": int(d["g"].nunique())}, None, significant=None) | {"table": {"columns": cols, "rows": rows, "total": total_groups}}


REGISTRY = {f.__name__: f for f in (distribution, outliers, category_share, group_compare, outcome_compare, robust_compare, pareto, trend, correlation, heatmap, segment_trend, scatter, composition, seasonality, summary_table, group_table)}


def spec_signature(spec: dict) -> str:
    return json.dumps([spec.get("tool"), sorted((spec.get("params") or {}).items())], default=str)


def validate_spec(spec: dict, roles: dict[str, str], uniques: dict[str, int]) -> tuple[dict | None, str]:
    """Reject anything an LLM may have hallucinated: unknown tools, unknown columns, wrong column types."""
    tool = spec.get("tool")
    params = spec.get("params") or {}
    if tool not in REGISTRY:
        return None, f"unknown tool '{tool}'"
    need = {
        "distribution": {"col": {"numeric"}}, "outliers": {"col": {"numeric"}},
        "category_share": {"col": {"categorical", "binary"}},
        "group_compare": {"target": {"numeric", "binary"}, "group": {"categorical", "binary"}},
        "outcome_compare": {"target": {"binary"}, "col": {"numeric"}},
        "robust_compare": {"target": {"numeric"}, "group": {"categorical", "binary"}},
        "pareto": {"target": {"numeric"}, "group": {"categorical", "binary"}},
        "trend": {"target": {"numeric", "binary"}, "date": {"date"}},
        "correlation": {},
        "heatmap": {"target": {"numeric", "binary"}, "group_a": {"categorical", "binary"}, "group_b": {"categorical", "binary"}},
        "segment_trend": {"target": {"numeric", "binary"}, "date": {"date"}, "group": {"categorical", "binary"}},
        "scatter": {"x": {"numeric"}, "y": {"numeric"}},
        "composition": {"group": {"categorical", "binary"}, "by": {"categorical", "binary"}},
        "seasonality": {"target": {"numeric", "binary"}, "date": {"date"}},
        "summary_table": {},
        "group_table": {"target": {"numeric", "binary"}, "group": {"categorical", "binary"}},
    }[tool]
    clean = {}
    for k, allowed in need.items():
        col = params.get(k)
        if col not in roles:
            return None, f"{tool}: column '{col}' does not exist"
        if roles[col] not in allowed:
            return None, f"{tool}: column '{col}' has role '{roles[col]}', expected {sorted(allowed)}"
        if (k.startswith("group") or k == "by") and not (2 <= uniques.get(col, 0) <= 30):
            return None, f"{tool}: group column '{col}' has {uniques.get(col)} distinct values (need 2-30)"
        clean[k] = col
    if tool == "heatmap" and clean["group_a"] == clean["group_b"]:
        return None, "heatmap: group_a and group_b must be different columns"
    if tool == "composition" and clean["group"] == clean["by"]:
        return None, "composition: group and by must be different columns"
    if tool == "scatter" and clean["x"] == clean["y"]:
        return None, "scatter: x and y must be different columns"
    if tool == "group_compare" and params.get("within") in roles and roles[params["within"]] in ("categorical", "binary") and params.get("within_value") not in (None, ""):
        clean["within"], clean["within_value"] = params["within"], str(params["within_value"])
    if tool in ("correlation", "summary_table"):
        clean["cols"] = [c for c in (params.get("cols") or []) if roles.get(c) == "numeric"]
    return {"tool": tool, "params": clean, "why": str(spec.get("why", ""))[:240]}, "ok"
