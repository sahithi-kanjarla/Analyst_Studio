"""A claim like "highest", "lowest", "biggest" or "most" in a summary or a brief must be true of the exact measure and grouping level it is about, and the sentence must name that level
("Furniture category", not just "Furniture"). The computed top and bottom come from the report's own checked facts (the ranking facts and the group comparisons); nothing is recalculated.
A line that gets the direction wrong is dropped. A line that names a group without its level gets the level added (the words around a figure change, the figure does not). Two lines of one text that make
the same kind of claim about the same measure and level for different groups conflict, and the later one is dropped. Only text the AI wrote is checked this way; the report's own sentences are never altered."""
from __future__ import annotations

import re

HIGH = {"highest", "biggest", "largest", "most", "top", "best", "greatest", "leading", "maximum", "strongest"}
LOW = {"lowest", "smallest", "least", "fewest", "worst", "weakest", "bottom", "minimum"}
NEAR = 70             # characters between a claim word and the group it is about


def table(result: dict) -> dict[str, dict]:
    """{fact id: {measure, level, top, bottom}} for every fact that has a computed top and bottom of one measure over one grouping level."""
    out: dict[str, dict] = {}
    for r in (result.get("summary_facts") or {}).get("rankings") or []:
        if r.get("id") and r.get("top") and r.get("bottom") and r.get("group"):
            out[r["id"]] = {"measure": str(r.get("measure") or ""), "level": str(r["group"]), "top": str(r["top"]), "bottom": str(r["bottom"])}
    for f in result.get("findings") or []:
        st, p = f.get("stats") or {}, f.get("params") or {}
        if f.get("id") and f.get("tool") == "group_compare" and st.get("best") and st.get("worst") and p.get("group"):
            out.setdefault(f["id"], {"measure": str(p.get("target") or ""), "level": str(p["group"]), "top": str(st["best"]), "bottom": str(st["worst"])})
    return out


def _claims(text: str) -> list[tuple[str, int]]:
    low = text.lower()
    return [(m.group(0), m.start()) for m in re.finditer(r"\b[a-z]+\b", low) if m.group(0) in HIGH | LOW]


def _near(text: str, value: str, direction_words: set[str]) -> bool:
    low, v = text.lower(), value.lower()
    i = low.find(v)
    if i < 0:
        return False
    return any(abs(pos - i) <= NEAR and w in direction_words for w, pos in _claims(text))


def _names_level(text: str, level: str) -> bool:
    return bool(re.search(rf"\b{re.escape(level.lower().rstrip('s'))}", text.lower()))


def check(lines: list[dict], result: dict, facts: dict | None = None) -> tuple[list[dict], list[str]]:
    """(lines that passed, possibly with the level added; the reasons for the ones dropped). Each line is {"text", "ids"}."""
    tab = table(result)
    kept: list[dict] = []
    dropped: list[str] = []
    seen: dict[tuple, tuple[str, str]] = {}                 # (measure, level, direction) -> (value, text) of the first claim
    for ln in lines:
        text = ln["text"]
        claims = _claims(text)
        if not claims:
            kept.append(ln)
            continue
        ok, why = True, ""
        new_text = text
        for fid in ln.get("ids") or []:
            t = tab.get(fid)
            if not t:
                continue
            for value, truthful, other in ((t["top"], HIGH, LOW), (t["bottom"], LOW, HIGH)):
                if value.lower() in text.lower():
                    if _near(text, value, other) and not _near(text, value, truthful):
                        ok, why = False, f"it says {value} is {'lowest' if truthful is HIGH else 'highest'}, but by {t['measure'] or 'that measure'} across {t['level']} {value} is {'highest' if truthful is HIGH else 'lowest'}"
                        break
                    if (_near(text, value, truthful)) and not _names_level(new_text, t["level"]):
                        new_text = re.sub(re.escape(value), f"{value} {t['level']}", new_text, count=1, flags=re.I)         # name the level: "Furniture category"
                    if _near(text, value, truthful):
                        key = (t["measure"].lower(), t["level"].lower(), "high" if truthful is HIGH else "low")
                        if key in seen and seen[key][0].lower() != value.lower():
                            ok, why = False, f"it conflicts with an earlier line that names {seen[key][0]} for the same claim about {t['measure'] or 'the same measure'} across {t['level']}"
                            break
                        seen.setdefault(key, (value, text))
            if not ok:
                break
        if not ok:
            dropped.append(why)
            continue
        kept.append({**ln, "text": new_text} if new_text != text else ln)
    return kept, dropped
