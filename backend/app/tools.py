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

from .config import CHART_DPI

PALETTE = ["#4F6BED", "#22A699", "#F2994A", "#E5484D", "#8E6BEE", "#3FA9F5", "#A3A847", "#C45AB3"]
plt.rcParams.update({
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "axes.grid.axis": "y", "grid.alpha": 0.25,
    "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "figure.dpi": 100,
    "axes.prop_cycle": matplotlib.cycler(color=PALETTE),
    # fallbacks so non-English column names (Hindi, Tamil, Chinese, Arabic...) render wherever the system has such a font
    "font.family": "sans-serif",
    "font.sans-serif": ["Inter", "DejaVu Sans", "Noto Sans", "Noto Sans Devanagari", "Nirmala UI", "Segoe UI", "Arial Unicode MS", "Noto Sans CJK SC", "Arial"],
})
SUM_WORDS = re.compile(r"revenue|sales|amount|total|count|qty|quantity|spend|cost|profit|units|orders", re.I)
AVG_WORDS = re.compile(r"pct|percent|rate|ratio|discount|avg|average|score|margin|price|per_|_per|age$", re.I)  # never add these up


def additive(name: str) -> bool:
    """Totals make sense for this column (so a trend or group table sums it); ratios, rates and prices are averaged instead."""
    return bool(SUM_WORDS.search(name)) and not AVG_WORDS.search(name)

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

MONEY_COL = re.compile(r"revenue|sales|amount|price|cost|spend|profit|income|salary|fee|payment|value|gmv|mrr|arr|budget|balance", re.I)


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
    return sym if sym and MONEY_COL.search(str(col)) else ""


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


# ---------------------------------------------------------------- tools
def distribution(df, ctx, col):
    x = pd.to_numeric(df[col], errors="coerce").dropna()
    if len(x) < 5:
        return None
    skew = float(x.skew())
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    ax.hist(x, bins=min(40, max(8, int(np.sqrt(len(x))))), color=PALETTE[0], alpha=0.85)
    ax.axvline(x.median(), color=PALETTE[3], ls="--", lw=1.5, label=f"median {_cur(col, ctx)}{_fmt(x.median())}")
    ax.axvline(x.mean(), color=PALETTE[1], ls=":", lw=1.8, label=f"mean {_cur(col, ctx)}{_fmt(x.mean())}")
    q1, q3 = float(x.quantile(0.25)), float(x.quantile(0.75))
    _title(ax, f"{col}: half of all rows fall between {_cur(col, ctx)}{_fmt(q1)} and {_cur(col, ctx)}{_fmt(q3)}")
    ax.set(xlabel=col, ylabel="Count")
    ax.legend(frameon=False)
    shape = "right-skewed (a few very large values)" if skew > 1 else "left-skewed" if skew < -1 else "roughly symmetric"
    text = (f"**{col}** has a median of {_fmt(x.median())} and mean of {_fmt(x.mean())} (range {_fmt(x.min())} to {_fmt(x.max())}); "
            f"the distribution is {shape}." + (" Medians are a safer summary than means here." if abs(skew) > 1 else ""))
    return finding("distribution", f"Distribution of {col}", text,
                   {"n": int(len(x)), "mean": _f(x.mean()), "median": _f(x.median()), "std": _f(x.std()), "skew": _f(skew), "q1": _f(q1), "q3": _f(q3)},
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
    vc = df[col].astype(str).value_counts()
    if len(vc) < 2:
        return None
    top = vc.head(10)
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    ax.barh(top.index[::-1], top.values[::-1], color=PALETTE[1])
    ax.set(xlabel="Rows")
    _title(ax, f"{col}: which values appear most often (top {len(top)} of {len(vc)})")
    share = top.iloc[0] / vc.sum()
    text = f"**{col}** has {len(vc)} distinct values; the most common is '{top.index[0]}' ({share:.0%} of rows)."
    return finding("category_share", f"Composition of {col}", text,
                   {"distinct": int(len(vc)), "top": {str(k): int(v) for k, v in top.head(5).items()}},
                   _save(fig, ctx, _slug("share", col)))


def _group_frame(df, target, group):
    y, kind, pos = target_values(df[target])
    d = pd.DataFrame({"g": df[group].astype(str), "y": y}).dropna()
    keep = d["g"].value_counts().head(12).index
    return d[d["g"].isin(keep)], kind, pos


def group_compare(df, ctx, target, group):
    d, kind, pos = _group_frame(df, target, group)
    if d["g"].nunique() < 2 or len(d) < 10:
        return None
    n_groups = d["g"].nunique()
    cur = _cur(target, ctx)
    if kind == "binary":
        agg = d.groupby("g")["y"].agg(["mean", "count"]).sort_values("mean", ascending=False)
        ct = pd.crosstab(d["g"], d["y"])
        if min(ct.shape) < 2:
            return None
        chi2, p, _, _ = stats.chi2_contingency(ct)
        v = float(np.sqrt(chi2 / (len(d) * (min(ct.shape) - 1))))
        label = f"'{pos}' rate"
        fig, ax = plt.subplots(figsize=(6.5, 3.8))
        ax.bar(agg.index, agg["mean"] * 100, color=PALETTE[0])
        ax.set(title=f"{target} ('{pos}') rate by {group}", ylabel="% of rows", xlabel=group)
        top, bot = agg.index[0], agg.index[-1]
        text = (f"The {label} for **{target}** is highest in **{group} = {top}** ({agg['mean'].iloc[0]:.1%}) and lowest in **{bot}** ({agg['mean'].iloc[-1]:.1%}). "
                f"The difference is {'statistically significant' if p < 0.05 else 'not statistically significant'} (chi-square p{_pf(p)}, Cramér's V={v:.2f}, {effect_label(v * v)} effect).")
        st = {"kind": "binary", "positive": str(pos), "p": _f(p), "effect": _f(v), "effect_name": "cramers_v", "n": int(len(d)), "groups": int(n_groups),
              "min_group_n": int(agg["count"].min()), "best": str(top), "worst": str(bot),
              "values": {str(k): _f(r) for k, r in agg["mean"].items()}}
        hi_lo = (agg["mean"].iloc[0], agg["mean"].iloc[-1])
    else:
        agg = d.groupby("g")["y"].agg(["mean", "median", "count", "sem"]).sort_values("mean", ascending=False)
        groups = [g["y"].values for _, g in d.groupby("g") if len(g) >= 2]
        if len(groups) < 2:
            return None
        p = float(stats.f_oneway(*groups)[1])
        grand = d["y"].mean()
        ssb = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
        sst = float(((d["y"] - grand) ** 2).sum())
        eta2 = float(ssb / sst) if sst > 0 else 0.0
        fig, ax = plt.subplots(figsize=(6.5, 3.8))
        ax.bar(agg.index, agg["mean"], yerr=1.96 * agg["sem"].fillna(0), color=PALETTE[0], capsize=3, error_kw=dict(alpha=0.6))
        ax.set(title=f"Average {target} by {group}", ylabel=f"Mean {target}", xlabel=group)
        top, bot = agg.index[0], agg.index[-1]
        text = (f"Average **{target}** is highest for **{group} = {top}** ({cur}{_fmt(agg['mean'].iloc[0])}) and lowest for **{bot}** ({cur}{_fmt(agg['mean'].iloc[-1])}). "
                f"The difference is {'statistically significant' if p < 0.05 else 'not statistically significant'} (ANOVA p{_pf(p)}, eta²={eta2:.3f}, {effect_label(eta2)} effect).")
        st = {"kind": "numeric", "p": _f(p), "effect": _f(eta2), "effect_name": "eta_squared", "n": int(len(d)), "groups": int(n_groups),
              "min_group_n": int(agg["count"].min()), "best": str(top), "worst": str(bot), "target_skew": _f(d["y"].skew()),
              "values": {str(k): _f(r) for k, r in agg["mean"].items()}}
        hi_lo = (agg["mean"].iloc[0], agg["mean"].iloc[-1])
    # ---- chart polish: takeaway title, highlighted leader, value labels, sample size under every bar
    means = [float(v) for v in agg["mean"]]
    for i, patch in enumerate(ax.patches[:len(agg)]):
        patch.set_color(PALETTE[0] if i == 0 else "#B9C2E8")
    ax.bar_label(_bars(ax), labels=[f"{v:.0%}" if kind == "binary" else f"{cur}{_fmt(v)}" for v in means], padding=3, fontsize=8)
    ax.set_xticks(range(len(agg)))
    ax.set_xticklabels([f"{g}\n(n={int(c):,})" for g, c in zip(agg.index, agg["count"])])
    ax.margins(y=0.16)
    ax.grid(axis="x", visible=False)
    ratio = means[0] / means[-1] if kind != "binary" and means[-1] > 0 and means[0] > 0 else None
    if kind == "binary":
        headline = f"{top} has the highest {target} rate ({means[0]:.0%}) vs {means[-1]:.0%} for {bot}"
    elif ratio and ratio >= 1.15:
        headline = f"{top} averages {ratio:.1f}× the {target} of {bot}"
    else:
        headline = f"Average {target} by {group}"
    _title(ax, headline + ("" if p < 0.05 else " (difference not significant)"))
    if n_groups > 6:
        plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    eff = st["effect"] or 0
    # Statistical significance is not practical significance: only recommend action on a medium-or-larger effect.
    meaningful = p < 0.05 and (eff >= 0.2 if kind == "binary" else eff >= 0.06)
    action = None
    if meaningful:
        # Neutral wording: for a binary outcome (churn, default, ...) "highest" can be the bad end, so don't call it "strongest".
        what = f"the highest rate of {target} = {pos}" if kind == "binary" else f"the highest {target}"
        action = (f"**{group} = {top}** has {what}, versus **{bot}** at the lowest; investigate what separates them. "
                  f"This is an association, so test any change (e.g. a targeted pilot) before assuming cause.")
    return finding("group_compare", f"{target} by {group}", text, st, _save(fig, ctx, _slug("cmp", target, group)),
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
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    d.boxplot(column="y", by="g", ax=ax, showfliers=False, grid=False)
    ax.set(xlabel=group, ylabel=target)
    _title(ax, f"Median {target} across {group} (outlier-resistant check)")
    fig.suptitle("")
    text = (f"Robustness check for **{target}** by **{group}** using medians: highest median in **{med.index[0]}** ({_fmt(med['median'].iloc[0])}), "
            f"lowest in **{med.index[-1]}** ({_fmt(med['median'].iloc[-1])}). Kruskal-Wallis p{_pf(p)} - "
            f"{'the group difference holds up without relying on means' if p < 0.05 else 'the difference does NOT hold up with a rank-based test'}.")
    return finding("robust_compare", f"Robustness check: {target} by {group}", text,
                   {"p": _f(p), "n": int(len(d)), "kind": "robust", "target": target, "group": group},
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
    k = min(3, len(g))
    top_share = float(share.head(k).sum())
    shown = g.head(15)
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    ax.bar(shown.index, shown.values, color=PALETTE[1])
    ax2 = ax.twinx()
    ax2.plot(shown.index, share.cumsum().head(15).values * 100, color=PALETTE[3], marker="o", ms=3)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Cumulative %")
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    for i, patch in enumerate(ax.patches[:len(shown)]):  # highlight the top-k contributors
        patch.set_color(PALETTE[1] if i < k else "#BFE3DE")
    ax.bar_label(_bars(ax), labels=[f"{s:.0%}" for s in share.head(15)], padding=2, fontsize=7)
    ax.set(ylabel=f"Total {_cur(target, ctx)}{target}".strip())
    ax.margins(y=0.12)
    ax.grid(axis="x", visible=False)
    _title(ax, f"The top {k} {group} values drive {top_share:.0%} of total {target}")
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    text = f"The top {k} values of **{group}** account for **{top_share:.0%}** of total {target} (leader: {g.index[0]}, {share.iloc[0]:.0%})."
    action = (f"{target} is concentrated in a few {group} values ({top_share:.0%} from the top {k}); protect them and look for growth in the long tail."
              if top_share > 0.5 and len(g) >= 5 else None)
    return finding("pareto", f"Where {target} comes from ({group})", text,
                   {"top_k": k, "top_share": _f(top_share), "groups": int(len(g)), "n": int(len(d))},
                   _save(fig, ctx, _slug("par", target, group)), action=action)


def trend(df, ctx, target, date):
    y, kind, _ = target_values(df[target])
    d = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "y": y}).dropna().set_index("t").sort_index()
    if len(d) < 10:
        return None
    span = (d.index.max() - d.index.min()).days
    rule = "D" if span <= 45 else "W" if span <= 180 else "MS"
    agg = "sum" if (kind == "numeric" and additive(target)) else "mean"
    series = d["y"].resample(rule).agg(agg)
    if agg == "mean":
        series = series.dropna()
    if len(series) < 4:
        return None
    x = np.arange(len(series))
    lr = stats.linregress(x, series.values)
    base = abs(series.mean()) or 1.0
    change = lr.slope * (len(series) - 1) / base
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.plot(series.index, series.values, color=PALETTE[0], marker="o", ms=3, lw=1.8)
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
    _title(ax, f"{target} is trending {'up' if lr.slope > 0 else 'down'} ({change:+.0%} over the period)" if sig else f"No clear trend in {target} over time (p={lr.pvalue:.2f})")
    text = (f"**{target}** ({agg} per {unit}) shows a {'statistically significant ' if sig else 'weak, non-significant '}{direction} trend "
            f"(about {change:+.0%} across the period, p{_pf(lr.pvalue)}). Best {unit}: {series.idxmax().date()} ({cur}{_fmt(series.max())}); weakest: {series.idxmin().date()} ({cur}{_fmt(series.min())}). "
            f"The first and last {unit}s may be partial.")
    action = (f"{target} is trending {direction} over time; find out what changed around the weakest {unit} ({series.idxmin().date()})." if sig else None)
    return finding("trend", f"{target} over time", text,
                   {"p": _f(lr.pvalue), "slope": _f(lr.slope), "relative_change": _f(change), "periods": int(len(series)), "freq": rule, "agg": agg, "n": int(len(d))},
                   _save(fig, ctx, _slug("trend", target, date)), significant=bool(sig), action=action)


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
    text = f"Strongest relationships: {listing}. Correlation shows columns move together, not that one causes the other."
    action = f"{strong[0][0]} and {strong[0][1]} are strongly related (r={strong[0][2]:.2f}); consider whether one can serve as a lever or an early indicator for the other." if strong else None
    return finding("correlation", "Relationships between numeric columns", text,
                   {"top_pairs": {f"{a}|{b}": _f(v) for (a, b), v in pairs.items()}, "n": int(len(num))},
                   _save(fig, ctx, "correlation"), action=action)


def heatmap(df, ctx, target, group_a, group_b):
    """Target by every combination of two categorical columns. Cells with fewer than 10 rows are blanked, not guessed.
    Cell-level gaps are NOT individually tested, so this tool is exploratory (significant=None)."""
    if group_a == group_b:
        return None
    y, kind, pos = target_values(df[target])
    d = pd.DataFrame({"a": df[group_a].astype(str), "b": df[group_b].astype(str), "y": y}).dropna()
    for c in ("a", "b"):
        d = d[d[c].isin(d[c].value_counts().head(8).index)]
    if len(d) < 30:
        return None
    piv = d.pivot_table(index="a", columns="b", values="y", aggfunc="mean")
    cnt = d.pivot_table(index="a", columns="b", values="y", aggfunc="count").reindex(index=piv.index, columns=piv.columns).fillna(0)
    shown = piv.where(cnt >= 10)
    if shown.shape[0] < 2 or shown.shape[1] < 2 or int(shown.notna().sum().sum()) < 4:
        return None
    shown = shown.loc[shown.mean(axis=1).sort_values(ascending=False).index, shown.mean(axis=0).sort_values(ascending=False).index]
    cnt = cnt.loc[shown.index, shown.columns]
    cells = shown.stack()
    (a_hi, b_hi), (a_lo, b_lo) = cells.idxmax(), cells.idxmin()
    hi, lo = float(cells.max()), float(cells.min())
    cur = _cur(target, ctx)
    fmt = (lambda v: f"{v:.0%}") if kind == "binary" else (lambda v: f"{cur}{_fmt(v)}")
    fig, ax = plt.subplots(figsize=(max(5.6, 1.05 * shown.shape[1] + 3.2), max(3.4, 0.6 * shown.shape[0] + 2.2)))
    cmap = matplotlib.colormaps["Blues"].with_extremes(bad="#EEF0F4")
    ax.imshow(np.ma.masked_invalid(shown.values.astype(float)), cmap=cmap, aspect="auto")
    ax.set_xticks(range(shown.shape[1]), [str(c) for c in shown.columns], rotation=30 if shown.shape[1] > 4 else 0, ha="right" if shown.shape[1] > 4 else "center")
    ax.set_yticks(range(shown.shape[0]), [str(r) for r in shown.index])
    ax.set(xlabel=group_b, ylabel=group_a)
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
    text = (f"Across **{group_a}** and **{group_b}**, {'the rate of ' + str(pos) + ' for ' if kind == 'binary' else ''}**{target}** is highest for **{a_hi} × {b_hi}** ({fmt(hi)}) "
            f"and lowest for **{a_lo} × {b_lo}** ({fmt(lo)}){gap}. Only combinations with at least 10 rows are shown; these cell-level gaps are not individually "
            f"tested, so treat them as leads to check rather than proven differences.")
    return finding("heatmap", f"{target}: {group_a} × {group_b}", text,
                   {"kind": "heatmap", "n": int(len(d)), "cells": int(cells.size), "positive": str(pos) if kind == "binary" else None, "target_kind": kind,
                    "top_cell": [str(a_hi), str(b_hi)], "top_value": _f(hi), "bottom_cell": [str(a_lo), str(b_lo)], "bottom_value": _f(lo)},
                   _save(fig, ctx, _slug("heat", target, group_a, group_b)), significant=None)


def segment_trend(df, ctx, target, date, group):
    """Target over time for each of the top groups. Per-group trend p-values are Bonferroni-corrected across the groups shown."""
    y, kind, _ = target_values(df[target])
    d = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "g": df[group].astype(str), "y": y}).dropna()
    if len(d) < 40:
        return None
    top = d["g"].value_counts().head(5)
    d = d[d["g"].isin(top[top >= 20].index)]
    if d["g"].nunique() < 2:
        return None
    span = (d["t"].max() - d["t"].min()).days
    rule = "D" if span <= 45 else "W" if span <= 180 else "MS"
    use_sum = kind == "numeric" and additive(target)
    series = {}
    for g, sub in d.groupby("g"):
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
        res[g] = {"p": float(lr.pvalue), "relative_change": float(lr.slope * (len(s) - 1) / (abs(s.mean()) or 1.0)), "periods": int(len(s))}
    k = len(res)
    for g in res:
        res[g]["p_adjusted"] = min(1.0, res[g]["p"] * k)
    fast = max(res, key=lambda g: res[g]["relative_change"])
    slow = min(res, key=lambda g: res[g]["relative_change"])
    sig_groups = [g for g in res if res[g]["p_adjusted"] < 0.05]
    survives = bool(sig_groups)
    unit = {"D": "day", "W": "week", "MS": "month"}[rule]
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    for i, (g, s) in enumerate(sorted(series.items(), key=lambda kv: -res[kv[0]]["relative_change"])):
        ax.plot(s.index, s.values, marker="o", ms=2.5, lw=2.0 if g in (fast, slow) else 1.2, color=PALETTE[i % len(PALETTE)], label=f"{g} ({res[g]['relative_change']:+.0%})")
    ax.legend(frameon=False, fontsize=8, title=f"{group} (change over period)", title_fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5))
    _dates(ax)
    ax.set_ylabel(f"{'Sum' if use_sum else 'Mean'} {target} per {unit}")
    _title(ax, (f"{fast} is moving up fastest ({res[fast]['relative_change']:+.0%}); {slow} is moving the other way ({res[slow]['relative_change']:+.0%})"
                if survives else f"{target} trends do not differ reliably across {group}"))
    desc = "; ".join(f"**{g}** {res[g]['relative_change']:+.0%} ({'significant' if res[g]['p_adjusted'] < 0.05 else 'not significant'})" for g in sorted(res, key=lambda g: -res[g]["relative_change"]))
    text = (f"Change in **{target}** over the period by **{group}** ({unit}ly, linear trend): {desc}. "
            f"P-values are corrected for {k} groups. The first and last {unit}s may be partial.")
    action = (f"**{fast}** and **{slow}** are moving in different directions for {target}; find out what is driving each before reallocating effort." if survives and fast != slow else None)
    return finding("segment_trend", f"{target} over time by {group}", text,
                   {"kind": "segment_trend", "groups": {g: {kk: _f(vv) for kk, vv in v.items()} for g, v in res.items()}, "fastest": str(fast), "slowest": str(slow),
                    "p": _f(min(v["p"] for v in res.values())), "p_adjusted": _f(min(v["p_adjusted"] for v in res.values())), "survives_correction": survives,
                    "n": int(len(d)), "freq": rule, "periods": int(min(v["periods"] for v in res.values()))},
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
    """How the mix of one category changes across another (100% stacked bars) with a chi-square test and Cramer's V."""
    if group == by:
        return None
    d = pd.DataFrame({"g": df[group].astype(str), "b": df[by].astype(str)})
    d = d[~d["g"].isin(["nan", "None", ""]) & ~d["b"].isin(["nan", "None", ""])]
    for c in ("g", "b"):
        d = d[d[c].isin(d[c].value_counts().head(8).index)]
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
    fig, ax = plt.subplots(figsize=(6.8, 0.55 * len(share) + 2.2))
    left = np.zeros(len(share))
    for i, col in enumerate(share.columns):
        ax.barh(share.index, share[col].values, left=left, color=PALETTE[i % len(PALETTE)], label=str(col), edgecolor="white", linewidth=0.6)
        for j, val in enumerate(share[col].values):
            if val >= 0.08:
                ax.text(left[j] + val / 2, j, f"{val:.0%}", ha="center", va="center", fontsize=7.5, color="white")
        left += share[col].values
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.invert_yaxis()
    ax.grid(False)
    ax.legend(title=by, frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=8, title_fontsize=8)
    sig = bool(p < 0.05)
    _title(ax, f"The mix of {by} differs across {group}" if sig else f"The mix of {by} is similar across {group}")
    text = (f"The mix of **{by}** {'differs' if sig else 'does not differ reliably'} across **{group}** (chi-square p{_pf(p)}, Cramér's V={v:.2f}, n={len(d):,}). "
            f"Biggest departure from the overall mix: **{big_g}** has {abs(big_diff):.0%} {'more' if big_diff > 0 else 'less'} {big_b} than average.")
    return finding("composition", f"{by} mix across {group}", text,
                   {"p": _f(p), "effect": _f(v), "effect_name": "cramers_v", "n": int(len(d)), "min_group_n": int(ct.sum(axis=1).min()),
                    "group": group, "by": by, "biggest": [str(big_g), str(big_b), _f(big_diff)]},
                   _save(fig, ctx, _slug("comp", group, by)), significant=sig,
                   action=(f"The {by} mix depends on {group}; tailor offers or channels by {group} rather than using one approach for all." if sig and v >= 0.2 else None))


def seasonality(df, ctx, target, date):
    """Does the target follow a weekday or month-of-year pattern? Tests each available cycle and corrects for testing two."""
    y, kind, _ = target_values(df[target])
    d = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "y": y}).dropna()
    if len(d) < 60:
        return None
    span = (d["t"].max() - d["t"].min()).days
    cycles = {}
    if span >= 56 and d["t"].dt.dayofweek.nunique() >= 5:
        cycles["weekday"] = (d["t"].dt.dayofweek, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    if span >= 700 and d["t"].dt.month.nunique() >= 10:   # under two years, a month pattern cannot be told apart from a trend
        cycles["month"] = (d["t"].dt.month, ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    if not cycles:
        return None
    results = {}
    for name, (key, labels) in cycles.items():
        grp = d.groupby(key)["y"]
        groups = [g.values for _, g in grp if len(g) >= 5]
        if len(groups) < 3:
            continue
        p = float(stats.kruskal(*groups)[1]) * len(cycles)
        means = grp.mean()
        grand = d["y"].mean()
        ssb = sum(len(g) * (g.mean() - grand) ** 2 for _, g in grp)
        sst = ((d["y"] - grand) ** 2).sum()
        results[name] = {"p": min(1.0, p), "means": means, "labels": labels, "eta": float(ssb / sst) if sst > 0 else 0.0, "min_n": int(grp.size().min())}
    if not results:
        return None
    name = min(results, key=lambda k: results[k]["p"])
    r = results[name]
    fig, axes = plt.subplots(1, len(results), figsize=(5.2 * len(results), 3.5), squeeze=False)
    for ax, (nm, rr) in zip(axes[0], results.items()):
        m = rr["means"]
        names = [rr["labels"][int(i) - (1 if nm == "month" else 0)] for i in m.index]
        bars = ax.bar(names, m.values, color=[PALETTE[1] if v == m.max() else PALETTE[3] if v == m.min() else "#C9D3F8" for v in m.values])
        ax.bar_label(bars, labels=[(f"{v:.0%}" if kind == "binary" else _fmt(v)) for v in m.values], padding=2, fontsize=7)
        ax.axhline(d["y"].mean(), color="#888", ls="--", lw=1)
        ax.margins(y=0.15)
        ax.grid(axis="x", visible=False)
        ax.set_ylabel(("Rate of " if kind == "binary" else "Average ") + target)
        _title(ax, f"{target} by {'day of week' if nm == 'weekday' else 'month of year'}", 40)
    sig = bool(r["p"] < 0.05)
    m = r["means"]
    hi, lo = r["labels"][int(m.idxmax()) - (1 if name == "month" else 0)], r["labels"][int(m.idxmin()) - (1 if name == "month" else 0)]
    fig.suptitle((f"{target} peaks on {hi} and is lowest on {lo}" if name == "weekday" else f"{target} peaks in {hi} and is lowest in {lo}") if sig else f"No reliable {name} pattern in {target}",
                 fontsize=12, fontweight="bold", x=0.02, ha="left", y=1.04)
    fmt = (lambda v: f"{v:.0%}") if kind == "binary" else _fmt
    unit = "day of the week" if name == "weekday" else "month of the year"
    text = (f"**{target}** {'varies reliably' if sig else 'does not vary reliably'} by {unit} (Kruskal-Wallis p{_pf(r['p'])}, effect size eta²={r['eta']:.3f}). "
            f"Highest: **{hi}** ({fmt(m.max())}); lowest: **{lo}** ({fmt(m.min())}); overall {fmt(d['y'].mean())}. Values are averages per row.")
    action = (f"{target} follows a {name} pattern; plan staffing, stock or promotions around {hi} (high) and {lo} (low)." if sig and r["eta"] >= 0.01 else None)
    vals = {r["labels"][int(i) - (1 if name == "month" else 0)]: _f(v) for i, v in m.items()}
    return finding("seasonality", f"{target} by {unit}", text,
                   {"p": _f(r["p"]), "effect": _f(r["eta"]), "effect_name": "eta_squared", "n": int(len(d)), "cycle": name, "best": hi, "worst": lo,
                    "values": vals, "min_group_n": r["min_n"], "kind": kind},
                   _save(fig, ctx, _slug("seas", target, date)), significant=sig, action=action)


def summary_table(df, ctx, cols=None):
    """A plain descriptive table of the numeric columns: no statistics claimed."""
    cols = [c for c in (cols or []) if c in df.columns]
    if not cols:
        cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    cols = cols[:12]
    rows = []
    for c in cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() == 0:
            continue
        rows.append([c, int(s.notna().sum()), f"{s.isna().mean():.0%}", _fmt(s.mean()), _fmt(s.median()), _fmt(s.std()), _fmt(s.min()), _fmt(s.max())])
    if not rows:
        return None
    text = f"Descriptive summary of {len(rows)} numeric column{'s' if len(rows) != 1 else ''} (count, share missing, mean, median, standard deviation, minimum and maximum)."
    return finding("summary_table", "Summary of numeric columns", text, {"n": int(len(df)), "columns": len(rows)},
                   None, significant=None) | {"table": {"columns": ["Column", "Count", "Missing", "Mean", "Median", "Std dev", "Min", "Max"], "rows": rows}}


def group_table(df, ctx, target, group):
    """Per-group table of the target: rows, average (or rate), median, total and share of total."""
    y, kind, _ = target_values(df[target])
    d = pd.DataFrame({"g": df[group].astype(str), "y": y}).dropna()
    d = d[~d["g"].isin(["nan", "None", ""])]
    if d["g"].nunique() < 2 or len(d) < 10:
        return None
    use_sum = kind == "numeric" and additive(target) and (d["y"] >= 0).all()
    g = d.groupby("g")["y"].agg(["count", "mean", "median", "sum"]).sort_values("sum" if use_sum else "mean", ascending=False).head(15)
    cur = _cur(target, ctx)
    cols = ["Group", "Rows", "Rate" if kind == "binary" else "Average"] + ([] if kind == "binary" else ["Median"]) + (["Total", "Share of total"] if use_sum else [])
    rows = []
    for name, r in g.iterrows():
        row = [name, int(r["count"]), f"{r['mean']:.1%}" if kind == "binary" else f"{cur}{_fmt(r['mean'])}"]
        if kind != "binary":
            row.append(f"{cur}{_fmt(r['median'])}")
        if use_sum:
            row += [f"{cur}{_fmt(r['sum'])}", f"{r['sum'] / d['y'].sum():.0%}"]
        rows.append(row)
    text = f"{target} for each {group} value" + (" ranked by total." if use_sum else " ranked by " + ("rate." if kind == "binary" else "average.")) + " Groups with few rows give unstable averages, so look at the Rows column first."
    return finding("group_table", f"{target} by {group} (table)", text, {"n": int(len(d)), "groups": int(d["g"].nunique())}, None, significant=None) | {"table": {"columns": cols, "rows": rows}}

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
    if tool in ("correlation", "summary_table"):
        clean["cols"] = [c for c in (params.get("cols") or []) if roles.get(c) == "numeric"]
    return {"tool": tool, "params": clean, "why": str(spec.get("why", ""))[:240]}, "ok"
