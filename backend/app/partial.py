"""The last period of a time series can be incomplete: the data may simply end part-way through a month or a week. A total over such a period against full ones says nothing about direction.

What this module does (all numbers by code, nothing knows a column):
  * `last_period`: the days the data reaches in the last period and the period's length, and whether it is incomplete (reaches under PARTIAL_BELOW of it)
  * `judge`: compares the incomplete period with the complete ones on a per-day basis. Verdicts:
        "normal"    its per-day rate is within the range of the complete periods (or within normal day-to-day noise): the lower total comes from the period being incomplete
        "fell"/"rose"  its per-day rate is outside that range and the period has at least MIN_DAYS days of data
        "too_short" it is outside the range but has fewer than MIN_DAYS days: no claim about direction
  * `sentence`: the one sentence that says it.
The coverage counts the days from the period's start to the last date in the data (the data reaching the period's end is what makes a period complete, even when some days had no rows)."""
from __future__ import annotations

import numpy as np
import pandas as pd

PARTIAL_BELOW = 0.8          # a last period reached on less than this share of its days is incomplete
MIN_DAYS = 7                 # fewer days than this cannot show a direction
MIN_COMPLETE = 3             # complete periods needed to say what a normal day looks like
NOISE_SE = 2.0               # a per-day rate within this many standard errors of the usual one is normal


def bounds(label: pd.Timestamp, rule: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(first day, last day) of the period a resample label stands for: a month starts on its label, a week ends on it."""
    label = pd.Timestamp(label).normalize()
    if rule == "MS":
        return label, label + pd.offsets.MonthEnd(0)
    if rule == "W":
        return label - pd.Timedelta(days=6), label
    return None


def last_period(dates: pd.Series | pd.DatetimeIndex, rule: str, label) -> dict | None:
    """{"label", "start", "end", "days_in_period", "days", "coverage", "partial", "data_end"} for the last period; None when periods are single days or the label is not a period."""
    b = bounds(label, rule)
    if b is None:
        return None
    start, end = b
    data_end = pd.Timestamp(pd.to_datetime(dates).max()).normalize()
    n = (end - start).days + 1
    days = int(min(max((data_end - start).days + 1, 1), n))
    cov = days / n
    return {"label": str(start.date()), "start": str(start.date()), "end": str(end.date()), "days_in_period": int(n), "days": days, "coverage": round(cov, 4), "partial": bool(cov < PARTIAL_BELOW), "data_end": str(data_end.date())}


def days_covered(label, rule: str, data_start: pd.Timestamp, data_end: pd.Timestamp) -> int:
    """How many days of the period the data covers (the first period may start before the data does)."""
    b = bounds(label, rule)
    lo, hi = max(b[0], pd.Timestamp(data_start).normalize()), min(b[1], pd.Timestamp(data_end).normalize())
    return max((hi - lo).days + 1, 1)


def judge(rates: list[float], daily: pd.Series, partial_total: float, days: int) -> dict:
    """Per-day comparison. `rates`: per-day rate of each complete period; `daily`: the total of every calendar day of those periods (a day with no rows is 0);
    `partial_total` and `days`: the incomplete period's total and the days it covers."""
    r = partial_total / max(days, 1)
    out = {"per_day": r, "days": int(days), "typical_per_day": None, "low": None, "high": None, "verdict": "too_short"}
    if len(rates) < MIN_COMPLETE or len(daily) < 2:
        return out
    mu, lo, hi = float(np.mean(rates)), float(min(rates)), float(max(rates))
    se = float(daily.std(ddof=1)) / np.sqrt(max(days, 1))
    out.update({"typical_per_day": mu, "low": lo, "high": hi})
    if lo <= r <= hi or abs(r - mu) <= NOISE_SE * se:
        out["verdict"] = "normal"
    elif days < MIN_DAYS:
        out["verdict"] = "too_short"
    else:
        out["verdict"] = "fell" if r < mu else "rose"
    return out


def analyse(rows: pd.Series, series: pd.Series, rule: str) -> tuple[dict, dict] | None:
    """(info, verdict) for the last period of a per-period SUM `series`, from the rows' values (`rows`, indexed by date); None when that period is complete or there are too few complete periods before it."""
    info = last_period(rows.index, rule, series.index[-1])
    if not info or not info["partial"] or len(series) - 1 < MIN_COMPLETE + 1:
        return None
    done = series.iloc[:-1]
    lo, hi = rows.index.min(), rows.index.max()
    rates = [float(v) / days_covered(i, rule, lo, hi) for i, v in done.items()]
    first_day = max(bounds(done.index[0], rule)[0], pd.Timestamp(lo).normalize())
    days_idx = pd.date_range(first_day, bounds(done.index[-1], rule)[1])
    daily = rows.groupby(rows.index.normalize()).sum().reindex(days_idx, fill_value=0.0)
    return info, judge(rates, daily, float(series.iloc[-1]), info["days"])


def sentence(info: dict, verdict: dict, target: str, unit: str, fmt, cur: str = "") -> str:
    """The one sentence the per-day comparison supports. `info` is `last_period`'s result, `verdict` is `judge`'s."""
    when = pd.Timestamp(info["start"]).strftime("%B %Y") if unit == "month" else f"the week ending {info['end']}"
    head = f"The last {unit} ({when}) has data for {info['days']} of {info['days_in_period']} days (data ends {info['data_end']}), so its total is not comparable with a full {unit}."
    pd_ = f"{cur}{fmt(verdict['per_day'])}"
    rng = f"{cur}{fmt(verdict['low'])} to {cur}{fmt(verdict['high'])}" if verdict.get("low") is not None else ""
    v = verdict["verdict"]
    if v == "normal":
        return f"{head} Per day it is {pd_}, within the {rng} per day of earlier {unit}s: the lower total comes from the {unit} being incomplete, not from a fall in {target}."
    if v == "fell":
        return f"{head} Per day it is {pd_}, below the {rng} per day of earlier {unit}s, so {target} per day fell and this is not only the {unit} being incomplete."
    if v == "rose":
        return f"{head} Per day it is {pd_}, above the {rng} per day of earlier {unit}s, so {target} per day rose."
    return f"{head} With only {info['days']} day{'s' if info['days'] != 1 else ''} of data it is too short to say whether {target} per day went up or down."


def idea_ok(st: dict) -> bool:
    """May an idea be built on this trend? Its fit already uses complete periods only; it is refused when the per-day rate of the incomplete period points the other way (the trend would be contradicted by the newest data)."""
    pt = st.get("partial")
    if not pt:
        return True
    up = (st.get("slope") or 0) > 0
    v = pt.get("verdict")
    return not ((v == "fell" and up) or (v == "rose" and not up))
