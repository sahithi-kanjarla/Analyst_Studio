"""What the Report's "What matters" cards show, built by code from the saved findings.

Nothing here computes a new statistic and nothing calls a model: it picks figures the finding already holds
(its group values, first and last period, share, rho), words them in plain language, and orders the findings
so different themes appear before one repeats. The selection of findings stays the one in curate.py.
"""
from __future__ import annotations

import re
from datetime import datetime

THEMES = ("leaders", "time", "link", "concentration")       # the order a reader meets them in
LEADERS = {"group_compare", "rate_compare", "ranking", "group_table", "robust_compare"}
TIME = {"trend", "seasonality", "growth", "segment_trend"}
LINK = {"correlation", "scatter", "chi_square", "composition", "heatmap"}
CONC = {"pareto", "concentration", "category_share"}
BARS = 6

PLAIN_EFFECT = {"negligible": "a very small difference", "small": "a small difference", "medium": "a moderate difference", "large": "a big difference"}


def theme_of(tool: str) -> str:
    return "leaders" if tool in LEADERS else "time" if tool in TIME else "link" if tool in LINK else "concentration" if tool in CONC else "other"


def fmt(v: float, pct: bool = False) -> str:
    if pct:
        return f"{v * 100:.0f}%" if abs(v) <= 1 else f"{v:.0f}%"
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else f"{v:,.1f}" if a >= 10 else f"{v:,.2f}"


def _measure(f: dict) -> str:
    p = f.get("params") or {}
    return str(p.get("target") or p.get("col") or p.get("y") or p.get("x") or "")


def _kind(f: dict) -> str:
    """The kind of comparison, so two findings of the same kind on the same measure count as one theme repeated."""
    t = f.get("tool", "")
    return "group" if t in ("group_compare", "rate_compare", "robust_compare", "ranking", "group_table") else t


# ------------------------------------------------------------------ the plain line
_BAD = re.compile(r"=|\beffect\b|\beta\b|\bp\s*[<>]|\bp-value|\brho\b|cram[eé]r|spearman|anova|kruskal|chi-square|\bn\s*=", re.I)


def plain_line(text: str) -> str:
    """The finding's own sentence with statistics taken out; '' when it cannot be made clean (the card then shows no line)."""
    t = re.sub(r"[*_`]", "", str(text or ""))
    t = re.sub(r"\([^)]*(?:effect|\bp\s*[=<>]|rho|\beta|n\s*=|\br\s*=)[^)]*\)", "", t, flags=re.I)
    t = re.sub(r"[A-Za-z_ ]+\s=\s+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ;,")
    return "" if _BAD.search(t) else t


def clean_sentences(text: str, keep: int = 2, max_chars: int = 230) -> str:
    """The first sentences of a passage that carry no statistic, no '=' and no effect-size wording, in the passage's own words."""
    src = re.sub(r"[*_`#]", "", str(text or ""))
    out: list[str] = []
    for part in re.findall(r".+?[.!?]+(?=\s|$)|.+$", re.sub(r"\s+", " ", src).strip()):
        part = part.strip()
        if part and not _BAD.search(part) and len(" ".join(out + [part])) <= max_chars:
            out.append(part)
        if len(out) >= keep:
            break
    return " ".join(out)


# ------------------------------------------------------------------ one card
def _values(st: dict) -> dict[str, float]:
    return {str(k): float(v) for k, v in (st.get("values") or {}).items() if isinstance(v, (int, float))}


def _bars(vals: dict[str, float], pct: bool, total: int | None, ordered: bool) -> tuple[list[dict], int]:
    items = list(vals.items()) if ordered else sorted(vals.items(), key=lambda kv: -kv[1])
    return [{"label": k, "value": v, "text": fmt(v, pct)} for k, v in items[:BARS]], int(total or len(items))


def _month_label(key: str, multi_year: bool) -> str:
    try:
        d = datetime.fromisoformat(key[:10])
    except ValueError:
        return key
    return d.strftime("%b %y") if multi_year else d.strftime("%b")


def lead_for(f: dict, effect: str | None = None) -> dict:
    """{value, label, sign, line, bars, bars_total, series, ratio, difference} for one finding. Any part may be missing."""
    tool, st = f.get("tool", ""), f.get("stats") or {}
    out: dict = {"value": "", "label": "", "sign": 0, "line": "", "bars": [], "bars_total": 0, "series": [], "ratio": "", "difference": PLAIN_EFFECT.get(effect or "", "")}
    m = _measure(f)
    vals = _values(st)
    pct = st.get("kind") == "binary"
    if tool in ("group_compare", "rate_compare", "robust_compare") and vals and st.get("best") in vals and tool != "robust_compare":
        best, worst = str(st["best"]), str(st.get("worst") or "")
        top = vals[best]
        basis = "rate" if pct else ("overall figure" if st.get("ratio_of_sums") else "average per record")
        out.update(value=fmt(top, pct), label=f"{best}, {basis}", line=f"{best} {'has a rate of' if pct else 'averages'} {fmt(top, pct)}" + (f"; {worst} {'has' if pct else 'averages'} {fmt(vals[worst], pct)}." if worst in vals else "."))
        out["bars"], out["bars_total"] = _bars(vals, pct, st.get("total_groups"), False)
        if worst in vals and vals[worst] > 0 and top > 0 and not pct:
            out["ratio"] = f"The top group is {top / vals[worst]:.1f} times the lowest."
    elif tool == "seasonality" and vals and st.get("best") in vals:
        best, worst = str(st["best"]), str(st.get("worst") or "")
        top = vals[best]
        out.update(value=fmt(top), label=f"{best}, the highest {st.get('cycle') or 'period'} on average", line=f"{best} averages {fmt(top)}" + (f"; {worst} averages {fmt(vals[worst])}." if worst in vals else "."))
        out["series"] = [{"key": k, "label": k, "value": v} for k, v in vals.items()]       # every period, in its natural order, drawn as columns
    elif tool == "trend" and f.get("series"):
        first, last = st.get("first"), st.get("last")
        ser = [(str(k), float(v)) for k, v in f["series"] if v is not None]
        multi = len({k[:4] for k, _ in ser}) > 1
        out["series"] = [{"key": k, "label": _month_label(k, multi), "value": v} for k, v in ser]
        if isinstance(first, (int, float)) and isinstance(last, (int, float)):
            if first > 0 and last > 0:
                ch = (last - first) / first * 100
                out.update(value=f"{ch:+.0f}%", label="Change from the first to the last period", sign=1 if ch > 0 else -1 if ch < 0 else 0)
            else:
                out.update(value=fmt(last), label=f"Last period, from {fmt(first)} in the first", sign=1 if last > first else -1 if last < first else 0)
            out["line"] = f"{m or 'The measure'} goes from {fmt(first)} in the first period to {fmt(last)} in the last."
    elif tool == "scatter" and st.get("rho") is not None:
        rho = float(st["rho"])
        out.update(value=f"{rho:+.2f}", label="How closely they move together (-1 to +1)", sign=1 if rho > 0 else -1 if rho < 0 else 0)
        out["line"] = f"{st.get('y') or 'One measure'} {'rises' if rho > 0 else 'falls'} as {st.get('x') or 'the other'} rises."
    elif tool == "pareto" and st.get("top_share") is not None:
        k = st.get("top_k")
        out.update(value=f"{float(st['top_share']) * 100:.0f}%", label=f"of the total from the top {k} {(f.get('params') or {}).get('group', 'groups')}")
        out["line"] = f"The top {k} {(f.get('params') or {}).get('group', 'groups')} bring {float(st['top_share']) * 100:.0f}% of {m or 'the total'}."
    if not out["line"]:
        out["line"] = plain_line((f.get("insight") or {}).get("headline") or f.get("text", ""))
    out["line"] = out["line"][:1].upper() + out["line"][1:]
    return out


# ------------------------------------------------------------------ the selection order
def diversify(pool: list[dict], findings: dict[str, dict], n: int) -> list[dict]:
    """n cards from the pool (already in curated order): one theme after another before any repeats, and never two cards with the same
    measure and the same kind of comparison while another card exists. The pool's own order breaks every tie."""
    def key(c):
        f = findings.get(c["id"]) or {}
        return (_measure(f), _kind(f))
    chosen: list[dict] = []
    left = list(pool)
    seen: set = set()
    for allow_repeat in (False, True):
        while left and len(chosen) < n:
            took = False
            for th in THEMES + ("other",):
                c = next((x for x in left if theme_of(x["tool"]) == th and (allow_repeat or key(x) not in seen)), None)
                if c is not None:
                    chosen.append(c)
                    left.remove(c)
                    seen.add(key(c))
                    took = True
                    if len(chosen) >= n:
                        break
            if not took:
                break
    return chosen


# ------------------------------------------------------------------ the actions
LEAD_WORDS = 12
LINE_WORDS = 24


def split_action(text: str) -> dict:
    """A short bold lead (at most about 12 words), one regular line, and the rest for the "Based on" fold. Only the action's own words are split."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    head, sep, tail = t.partition(":")
    if not sep:
        head, tail = t, ""
    mid, _, why = tail.rpartition(":")
    words = head.split()
    lead = " ".join(words[:LEAD_WORDS])
    spill = " ".join(words[LEAD_WORDS:])
    sentences = re.findall(r".+?[.!?]+(?=\s|$)|.+$", why.strip()) if why.strip() else []
    line = sentences[0].strip() if sentences else ""
    rest_sent = [x.strip() for x in sentences[1:]]
    w = line.split()
    if len(w) > LINE_WORDS:                       # a long sentence: the first clause stands as the line, the rest moves to the fold
        cut = line.rfind(",", 0, 160)
        first = line[:cut] if cut > 40 else " ".join(w[:LINE_WORDS])
        rest_sent.insert(0, line[len(first):].lstrip(", ").strip())
        line = first.rstrip(",;") + "."
    rest = " ".join(x for x in [spill, mid.strip(), *rest_sent] if x)
    return {"lead": lead, "line": line, "rest": rest}


def apply(view: dict, findings: list[dict]) -> None:
    """Add `lead` to every Report finding and put them in the diversified order, drawing on the 'more' findings when the kept ones repeat."""
    for a in (view.get("page") or {}).get("actions") or []:
        a.update({f"split_{k}": v for k, v in split_action(a.get("text", "")).items()})
    rf = view.get("report_findings")
    if not rf:
        return
    by_id = {f["id"]: f for f in findings}
    pool = list(rf) + [c for c in view.get("more_findings", []) if c["id"] not in {r["id"] for r in rf}]
    pool = [c for c in pool if c.get("tool") not in ("summary_table", "group_table")]
    kept = diversify(pool, by_id, len(rf))
    view["report_findings"] = kept
    for c in kept:
        lead = lead_for(by_id.get(c["id"]) or {"tool": c["tool"], "stats": {}, "text": c.get("headline", "")}, c.get("effect"))
        lead["why"] = clean_sentences(c.get("why_it_matters") or c.get("observation") or "")
        lead["merged"] = [it["text"] for it in (c.get("merged") or {}).get("items", []) if len(c["merged"]["items"]) > 1 and not _BAD.search(it.get("text", ""))]
        c["lead"] = lead
