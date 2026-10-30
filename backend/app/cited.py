"""Figures in AI-written prose must be tied to the fact they came from.
The prompt makes the model write a fact id in square brackets after every figure ("Set is 49.6% of amount [r1]"). Code then checks, per figure:
  1. a fact id is given and exists;
  2. the figure equals a figure of that fact (same rounding rules as `insights.unverified_numbers`);
  3. the words around the figure (6 either side) name the fact's subject;
  4. a percent figure from a growth or trend fact also has that fact's measure (and period) named near it, so a figure of another measure or period is not read as this one.
A sentence that fails any check is dropped by the caller, and the reason is recorded. `contradicts` separately catches a share that disagrees with a computed one."""
from __future__ import annotations

import re

from .insights import NUM_RE, SUFFIX_RE, allowed_numbers, unverified_numbers
from .summary import _same, tokens

CITE_RE = re.compile(r"\s*\[\s*([A-Za-z]\w*(?:\s*[,;]\s*[A-Za-z]\w*)*)\s*\]")
WINDOW = 6                  # words either side of a figure that must name the cited fact's subject
SCOPE_WINDOW = 9            # words either side of a percent figure in which the measure and period of a growth or trend fact must be named
CITE_RULE = ("Write the id of the fact a figure comes from in square brackets right after the figure, before any full stop, e.g. 'Set is 49.6% of amount [r1]'. "
             "Every number you write needs an id. A figure may only cite the fact it appears in, and the words around it must be about that fact's subject. "
             "A percent change must name the measure it is a change of, and the periods for a first-vs-last comparison or the word 'trend' for a fitted trend, right beside the figure. ")


def make_fact(numbers_from: str, subject_from: str, scope: tuple[str, str] = ("", "")) -> dict:
    """`scope` is (measure, period words) the fact's percent figures describe, from `scope_of`."""
    return {"numbers": allowed_numbers(numbers_from), "subject": tokens(subject_from), "measure": tokens(scope[0]), "period": tokens(scope[1])}


def scope_of(item: dict) -> tuple[str, str]:
    """(measure, period words) that the percent figures of a growth ranking fact or a trend finding describe; ("", "") for any other fact, whose figures need no scope."""
    if item.get("kind") == "growth" and item.get("periods"):
        return str(item.get("measure") or ""), " ".join(str(p) for p in item["periods"])
    st = item.get("stats") or {}
    if "trend" in str(st.get("kind", "")):
        p = item.get("params") or {}
        return str(p.get("target") or p.get("y") or p.get("col") or ""), "trend"
    return "", ""


def split_cites(text: str) -> tuple[str, list[tuple[int, list[str]]]]:
    """(text without the [id] markers, [(position in that text, ids)])."""
    text = re.sub(r"([.!?])\s*(\[\s*[A-Za-z]\w*[^\]]*\])", r"\2\1", str(text))      # "... [r1]." and "... . [r1]" both mean the id belongs before the stop
    clean, marks, last = [], [], 0
    for m in CITE_RE.finditer(text):
        clean.append(text[last:m.start()])
        marks.append((sum(len(p) for p in clean), [x.strip() for x in re.split(r"[,;]", m.group(1))]))
        last = m.end()
    clean.append(text[last:])
    return "".join(clean), marks


def _end(m: re.Match) -> int:
    """End of the figure itself: the number pattern also takes a full stop that closes the sentence."""
    return m.start() + len(m.group(0).rstrip(".,"))


def _figure_end(text: str, m: re.Match) -> int:
    end = _end(m)
    if suf := SUFFIX_RE.match(text, end):
        end = suf.end()
    return end + 1 if text[end:end + 1] == "%" else end


def _window(text: str, m: re.Match, size: int = WINDOW) -> set[str]:
    words = list(re.finditer(r"[A-Za-z0-9]+", text)) if size != WINDOW else list(re.finditer(r"[A-Za-z][A-Za-z0-9]*", text))
    before = [w.group(0) for w in words if w.end() <= m.start()][-size:]
    after = [w.group(0) for w in words if w.start() >= _end(m)][:size]
    return tokens(" ".join(before + after))


def verify(text: str, facts: dict[str, dict]) -> tuple[str, str | None]:
    """(text without the markers, None) when every figure is tied to a fact it matches, else (text without the markers, the reason)."""
    clean, marks = split_cites(text)
    for m in NUM_RE.finditer(clean):
        piece = clean[m.start():_figure_end(clean, m)]
        shown = piece.rstrip(".,")
        cite = next((ids for pos, ids in marks if pos >= _end(m)), None)
        if cite is None:
            return clean, f"{shown}: no fact id given"
        known = [i for i in cite if i in facts]
        if not known:
            return clean, f"{shown}: unknown fact id {', '.join(cite)}"
        pool = [n for i in known for n in facts[i]["numbers"]]
        if unverified_numbers(piece, pool):
            return clean, f"{shown}: not a figure of fact {', '.join(known)}"
        subject = set().union(*(facts[i]["subject"] for i in known))
        win = _window(clean, m)
        if subject and win and not any(_same(w, s) for w in win for s in subject):
            return clean, f"{shown}: the words around it are not about the subject of fact {', '.join(known)}"
        if piece.endswith("%"):
            near = _window(clean, m, SCOPE_WINDOW)
            for i in known:
                for what in ("measure", "period"):
                    if facts[i].get(what) and not all(any(_same(w, t) for w in near) for t in facts[i][what]):
                        return clean, f"{shown}: the {what} of fact {i} ({' '.join(sorted(facts[i][what]))}) is not named next to it"
    return clean, None


# ------------------------------------------------------------------ a share that disagrees with a computed one
LESS = re.compile(r"(?:less than|under|below|fewer than|at most|up to|no more than|<)\s*$", re.I)
MORE = re.compile(r"(?:more than|over|above|at least|exceed\w*|>)\s*$", re.I)


def claims(rankings: list[dict]) -> list[dict]:
    """Computed shares with the words that name what they are the share of: [{names, also, values, id}]. Values are fractions."""
    out = []
    for r in rankings:
        if r.get("kind") == "growth":       # the change of one group between the first and last period: the same group, measure and periods named with another figure is a conflict
            for x in r.get("groups") or []:
                if x.get("pct") is not None:
                    out.append({"id": r["id"], "names": tokens(str(x["group"])), "also": tokens(str(r["measure"])), "period": tokens(" ".join(r.get("periods") or [])), "values": [x["pct"]],
                                "text": f"{r['measure']} by {r['group']}, {' vs '.join(r.get('periods') or [])}: {x['line']}"})
        elif r.get("kind") == "outside_rate":
            vals = [v for v in (r.get("rate_amount"), r.get("rate_rows")) if v is not None]
            if vals:
                out.append({"id": r["id"], "names": tokens(" ".join([str(r["group"])] + [str(v) for v in r.get("values") or []])), "also": set(), "values": vals, "text": r["text"]})
        elif r.get("top_share") is not None:
            out.append({"id": r["id"], "names": tokens(str(r["top"])), "also": tokens(str(r["measure"])), "values": [r["top_share"]], "text": r.get("lead") or r["text"]})
    return out


def contradicts(clean: str, computed: list[dict]) -> dict | None:
    """The computed claim a percent figure in `clean` disagrees with (same subject, different share), else None."""
    sent = tokens(clean)
    for m in NUM_RE.finditer(clean):
        if clean[_end(m):_end(m) + 1] != "%":
            continue
        raw = m.group(0).replace(",", "").rstrip(".")
        try:
            v = float(raw) / 100
        except ValueError:
            continue
        half = 0.5 * 10 ** -(len(raw.split(".")[1]) if "." in raw else 0) / 100 + 1e-9
        win = _window(clean, m)
        for c in computed:
            if not any(_same(w, s) for w in win for s in c["names"]) or (c["also"] and not any(_same(w, s) for w in sent for s in c["also"])):
                continue
            if c.get("period") and not all(any(_same(w, t) for w in sent) for t in c["period"]):
                continue
            before = clean[max(0, m.start() - 20):m.start()]
            if LESS.search(before):
                bad = min(c["values"]) >= v - 1e-9
            elif MORE.search(before):
                bad = max(c["values"]) <= v + 1e-9
            else:
                bad = all(abs(v - x) > half for x in c["values"])
            if bad:
                return c
    return None
