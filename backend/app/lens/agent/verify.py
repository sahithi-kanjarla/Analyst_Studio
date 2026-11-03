"""Verification: a figure in any text a request shows must come from a tool result or the report's checked fact sheet, and pass the same fact-id citation check the report uses
(`cited.verify`: the id exists, the figure equals one of that fact's figures, the words around it are about its subject). A sentence that fails is dropped and the reason recorded.
Tool results become facts with their own ids (q1, c1, ...), so a reply or a brief can cite them."""
from __future__ import annotations

import re

from ... import cited

_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def next_id(env, prefix: str) -> str:
    n = sum(1 for k in env.facts if k.startswith(prefix) and k[len(prefix):].isdigit()) + 1
    return f"{prefix}{n}"


def fact_from_text(numbers_from: str, subject_from: str) -> dict:
    return cited.make_fact(numbers_from, subject_from)


def facts_from_answer(prefix: str, ans: dict, env) -> dict[str, dict]:
    """One fact for a computed answer: its figures are the cells of its table and the figures recorded with the sentence."""
    cells = []
    t = ans.get("table") or {}
    for r in t.get("shown") or t.get("rows") or []:
        cells += [str(c) for c in r]
    cells += [str(f.get("shown", "")) for f in ans.get("figures") or []]
    subject = " ".join([str(ans.get("text", ""))] + [str(c) for c in t.get("columns") or []] + [str(x) for r in (t.get("shown") or [])[:30] for x in r if not _NUM.fullmatch(str(x).strip().replace("%", ""))])
    fid = next_id(env, prefix)
    fact = fact_from_text(" ".join(cells) + " " + str(ans.get("text", "")), subject)
    env.facts[fid] = fact
    ans.setdefault("fact_ids", [])
    ans["fact_ids"] = list(dict.fromkeys(list(ans["fact_ids"]) + [fid]))
    return {fid: fact}


def facts_from_table(prefix: str, env, columns: list[str], rows: list[list], title: str = "") -> tuple[str, dict]:
    fid = next_id(env, prefix)
    cells = " ".join(str(c) for r in rows for c in r)
    subject = " ".join([title] + [str(c) for c in columns] + [str(x) for r in rows[:30] for x in r if isinstance(x, str)])
    env.facts[fid] = fact_from_text(cells, subject)
    return fid, env.facts[fid]


def check_sentence(text: str, facts: dict[str, dict]) -> tuple[str, str | None]:
    """(clean text, None) when every figure is tied to a fact it matches; a sentence without a figure passes."""
    if not re.search(r"\d", re.sub(r"\[\s*[A-Za-z]\w*(?:\s*[,;]\s*[A-Za-z]\w*)*\s*\]", "", text)):
        clean, _ = cited.split_cites(text)
        return clean.strip(), None
    clean, why = cited.verify(text, facts)
    return clean.strip(), why


def filter_lines(lines: list[str], facts: dict[str, dict], computed: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """([{"text", "ids"}] that passed, [reasons for the ones dropped])."""
    good, dropped = [], []
    for raw in lines:
        if not isinstance(raw, str) or not raw.strip():
            continue
        clean, why = check_sentence(raw, facts)
        _, marks = cited.split_cites(raw)
        ids = [i for _, g in marks for i in g if i in facts]
        if not why and computed and cited.contradicts(clean, computed):
            why = "a share that disagrees with the computed one"
        if why:
            dropped.append(why)
            continue
        good.append({"text": clean, "ids": list(dict.fromkeys(ids))})
    return good, dropped
