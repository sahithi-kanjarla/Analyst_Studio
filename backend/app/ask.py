"""'Ask about this report' (off unless CHATBOT=on). The AI only picks ONE operation from a closed list and its parameters. Code checks the
parameters against the run's real columns and values, runs the operation on the cleaned data, writes every sentence from the results and draws the chart.
Nothing an AI writes is run or shown as a figure. A question that fits no operation is said so, with two questions that it can answer."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pandas as pd

from . import llm as llmmod

OPS = ("top_n", "compare", "trend", "share", "filter_total")
OP_TEXT = {
    "top_n": 'the top N groups by the total of a measure, e.g. "which 3 stores earn the most revenue" -> {"op":"top_n","measure":"revenue","group":"Store","n":3}',
    "compare": "two groups side by side by the total of a measure -> {\"op\":\"compare\",\"measure\":...,\"group\":...,\"a\":...,\"b\":...}",
    "trend": "the total of a measure per month or quarter over time -> {\"op\":\"trend\",\"measure\":...,\"freq\":\"M\" or \"Q\"}",
    "share": "the share of the total one group value makes up -> {\"op\":\"share\",\"measure\":...,\"group\":...,\"value\":...}",
    "filter_total": "the total of a measure for rows where one column has one value -> {\"op\":\"filter_total\",\"measure\":...,\"column\":...,\"value\":...}",
}


def mode() -> str:
    """CHATBOT=auto (default), on or off. auto: on when an AI model is available, and always on for the sample run; on: always on; off: never."""
    v = os.environ.get("CHATBOT", "auto").strip().lower()
    return "on" if v in ("on", "1", "true", "yes") else "off" if v in ("off", "0", "false", "no") else "auto"


def ai_available() -> bool:
    return llmmod.get_llm()[0] is not None


def enabled(demo: bool = False) -> bool:
    m = mode()
    return m == "on" or (m == "auto" and (demo or ai_available()))


def _money(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


def _columns(result: dict, df: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    """(measures, groups, dates): the numeric, categorical and date columns of the cleaned table, in the profile's order."""
    roles = {c["name"]: c.get("role") for c in (result.get("profile") or {}).get("columns") or []}
    measures = [c for c in df.columns if roles.get(c) == "numeric"]
    groups = [c for c in df.columns if roles.get(c) == "categorical" and df[c].nunique() <= 60]
    dates = [c for c in df.columns if roles.get(c) == "date"]
    return measures, groups, dates


def _suggestions(measures: list[str], groups: list[str], dates: list[str]) -> list[str]:
    out = []
    if measures and groups:
        out.append(f"Which {groups[0]} earns the most {measures[0]}?")
    if measures and dates:
        out.append(f"How has {measures[0]} changed over time?")
    if measures and len(groups) > 1:
        out.append(f"Compare the total {measures[0]} of two {groups[0]} values.")
    return out[:2]


def _prompt(measures: list[str], groups: list[str], dates: list[str], values: dict[str, list[str]]) -> tuple[str, str]:
    system = (
        "You translate one question about a table into ONE operation from a fixed list, and its parameters. You never answer with numbers and never "
        "invent a column or a value. If no operation fits, answer {\"op\": \"none\"}.\n\nOperations:\n- " + "\n- ".join(OP_TEXT[o] for o in OPS)
    )
    lines = [f"measures (numbers): {measures}", f"groups (categories): {groups}", f"date columns: {dates}"]
    lines += [f"values of {g}: {values[g][:30]}" for g in groups[:6]]
    user = "\n".join(lines) + "\n\nQuestion: {q}\n\nReturn the JSON object only."
    return system, user


def _check(choice: dict, measures, groups, dates, values) -> tuple[dict | None, str]:
    """(operation, reason). The operation is None when the AI's choice does not hold up against this run's columns and values."""
    op = choice.get("op")
    if op not in OPS:
        return None, "no operation fits this question"
    m = choice.get("measure")
    if m not in measures:
        return None, f"'{str(m)[:40]}' is not a number column of this report"
    if op == "trend":
        if not dates:
            return None, "this data has no date column to show a trend over"
        freq = choice.get("freq") if choice.get("freq") in ("M", "Q") else "M"
        return {"op": op, "measure": m, "freq": freq, "date": dates[0]}, ""
    g = choice.get("group") or choice.get("column")
    if g not in groups:
        return None, f"'{str(g)[:40]}' is not a category column of this report"
    if op == "top_n":
        try:
            n = int(choice.get("n") or 5)
        except (TypeError, ValueError):
            n = 5
        return {"op": op, "measure": m, "group": g, "n": max(1, min(n, 10))}, ""
    values_of = {v.lower(): v for v in values[g]}
    def pick(key):
        v = values_of.get(str(choice.get(key, "")).strip().lower())
        return v
    if op == "compare":
        a, b = pick("a"), pick("b")
        if not a or not b or a == b:
            return None, f"the two values to compare were not found in '{g}'"
        return {"op": op, "measure": m, "group": g, "a": a, "b": b}, ""
    value = pick("value")
    if not value:
        return None, f"'{str(choice.get('value'))[:40]}' is not a value of '{g}'"
    return {"op": op, "measure": m, "group": g, "value": value} if op == "share" else {"op": op, "measure": m, "column": g, "value": value}, ""


def _run(op: dict, df: pd.DataFrame, chart_path: Path) -> dict:
    """Runs one operation on the cleaned data. Returns the sentence, the table and the chart; every figure is computed here."""
    from matplotlib.figure import Figure
    m = op["measure"]
    total = float(df[m].sum())
    fig = Figure(figsize=(7.2, 3.9), dpi=110)
    ax = fig.add_subplot()
    if op["op"] == "trend":
        s = df.groupby(df[op["date"]].dt.to_period(op["freq"]))[m].sum()
        rows = [[str(p), float(v)] for p, v in s.items()]
        peak = s.idxmax()
        text = (f"Total {m} by {'month' if op['freq'] == 'M' else 'quarter'}: from {_money(float(s.iloc[0]))} in {s.index[0]} to "
                f"{_money(float(s.iloc[-1]))} in {s.index[-1]}; the highest period is {peak} at {_money(float(s.max()))}.")
        ax.plot([str(p) for p in s.index], s.values, color="#a4461f", linewidth=2, marker="o", markersize=3)
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    elif op["op"] == "top_n":
        s = df.groupby(op["group"])[m].sum().sort_values(ascending=False)
        head = s.head(op["n"])
        rows = [[str(k), float(v), float(v) / total * 100 if total else 0.0] for k, v in head.items()]
        text = f"Top {op['n']} {op['group']} by total {m}: " + ", ".join(f"{k} {_money(v)} ({p:.1f}%)" for k, v, p in rows) + "."
        ax.barh([str(r[0]) for r in rows][::-1], [r[1] for r in rows][::-1], color=["#cfc7b9"] * (len(rows) - 1) + ["#a4461f"])
        for k, r in enumerate(rows[::-1]):
            ax.text(r[1], k, f" {_money(r[1])}", va="center", fontsize=8)
    elif op["op"] == "compare":
        s = df[df[op["group"]] == op["a"]][m].sum(), df[df[op["group"]] == op["b"]][m].sum()
        rows = [[op["a"], float(s[0])], [op["b"], float(s[1])]]
        diff = (s[0] - s[1]) / s[1] * 100 if s[1] else 0.0
        text = f"Total {m}: {op['a']} {_money(s[0])} against {op['b']} {_money(s[1])}, {op['a']} is {abs(diff):.1f}% {'higher' if diff >= 0 else 'lower'}."
        ax.bar([op["a"], op["b"]], [s[0], s[1]], color=["#a4461f", "#cfc7b9"])
        for k, v in enumerate((s[0], s[1])):
            ax.text(k, v, _money(v), ha="center", va="bottom", fontsize=8)
    elif op["op"] == "share":
        part = float(df[df[op["group"]] == op["value"]][m].sum())
        share = part / total * 100 if total else 0.0
        rows = [[op["value"], part, share]]
        text = f"{op['value']} accounts for {_money(part)} of the total {m} ({share:.1f}%)."
        ax.bar([op["value"], "all other"], [part, total - part], color=["#a4461f", "#cfc7b9"])
        for k, v in enumerate((part, total - part)):
            ax.text(k, v, _money(v), ha="center", va="bottom", fontsize=8)
    else:  # filter_total
        sel = df[df[op["column"]] == op["value"]]
        part = float(sel[m].sum())
        rows = [[op["value"], part, len(sel)]]
        text = f"For {op['column']} = {op['value']}: {len(sel):,} rows with a total {m} of {_money(part)}."
        ax.bar([str(op["value"])], [part], color="#a4461f")
        ax.text(0, part, _money(part), ha="center", va="bottom", fontsize=8)
    ax.set_title(m if op["op"] != "trend" else f"{m} over time", fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(chart_path, dpi=110, facecolor="#ffffff")
    return {"text": text, "rows": rows, "figures": {"total": total}}


def _prepare(workdir: Path, result: dict):
    df = pd.read_pickle(workdir / "clean.pkl")
    measures, groups, dates = _columns(result, df)
    values = {g: sorted(str(v) for v in df[g].dropna().unique()) for g in groups}
    return df, measures, groups, dates, values


def _main_measure(result: dict, measures: list[str]) -> str:
    target = (result.get("plan") or {}).get("target")
    return target if target in measures else measures[0]


def chips(workdir: Path, result: dict) -> list[dict]:
    """4 to 6 suggested questions written from this report's own data. Each carries the operation it maps to, so it runs without an AI model."""
    df, measures, groups, dates, _ = _prepare(workdir, result)
    if not measures:
        return []
    m = _main_measure(result, measures)
    out = []
    if groups:
        g = groups[0]
        order = df.groupby(g)[m].sum().sort_values(ascending=False)
        out.append({"text": f"Which {g} earns the most {m}?", "op": {"op": "top_n", "measure": m, "group": g, "n": 3}})
        if dates:
            out.append({"text": f"Is {m} growing?", "op": {"op": "trend", "measure": m, "freq": "M", "date": dates[0]}})
        if len(order) >= 2:
            a, b = str(order.index[0]), str(order.index[1])
            out.append({"text": f"Compare {a} and {b}", "op": {"op": "compare", "measure": m, "group": g, "a": a, "b": b}})
            out.append({"text": f"How much of the {m} comes from {a}?", "op": {"op": "share", "measure": m, "group": g, "value": a}})
        if len(groups) > 1:
            g2 = groups[1]
            order2 = df.groupby(g2)[m].sum().sort_values(ascending=False)
            out.append({"text": f"Which {g2} earns the most {m}?", "op": {"op": "top_n", "measure": m, "group": g2, "n": 3}})
            if len(order2):
                v2 = str(order2.index[0])
                out.append({"text": f"What is the total {m} for {g2} {v2}?", "op": {"op": "filter_total", "measure": m, "column": g2, "value": v2}})
    elif dates:
        out.append({"text": f"Is {m} growing?", "op": {"op": "trend", "measure": m, "freq": "M", "date": dates[0]}})
    for k, c in enumerate(out[:6]):
        c["id"] = f"c{k + 1}"
    return out[:6]


def _how(op: dict) -> dict:
    names = {"top_n": "Top groups by total", "compare": "Two groups compared by total", "trend": "Total over time", "share": "Share of the total", "filter_total": "Total for matching rows"}
    cols = [op["measure"]] + [op[k] for k in ("group", "column", "date") if op.get(k)]
    detail = {"top_n": f"Added up {op['measure']} for each {op.get('group')} and ranked them.", "compare": f"Added up {op['measure']} for {op.get('a')} and for {op.get('b')} in {op.get('group')}.",
              "trend": f"Added up {op['measure']} for each {'month' if op.get('freq') == 'M' else 'quarter'} of {op.get('date')}.",
              "share": f"Added up {op['measure']} for {op.get('value')} in {op.get('group')} and divided by the total.",
              "filter_total": f"Added up {op['measure']} for the rows where {op.get('column')} is {op.get('value')}."}[op["op"]]
    return {"operation": names[op["op"]], "columns": cols, "detail": detail + " Every figure is calculated by code from the cleaned data."}


def _execute(workdir: Path, df: pd.DataFrame, op: dict) -> dict:
    key = hashlib.sha1(repr(sorted(op.items())).encode()).hexdigest()[:12]
    chart = workdir / "charts" / f"ask_{key}.png"
    chart.parent.mkdir(exist_ok=True)
    out = _run(op, df, chart)
    return {"status": "answered", "op": op["op"], "text": out["text"], "rows": out["rows"], "chart": f"charts/{chart.name}", "how": _how(op), "suggestions": []}


def _norm(t: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in t).split())


def match_chip(question: str, all_chips: list[dict]) -> dict | None:
    """A typed question that clearly matches a chip (same words, or very close) runs that chip; anything else does not."""
    import difflib
    q = _norm(question)
    best, score = None, 0.0
    for c in all_chips:
        r = difflib.SequenceMatcher(None, q, _norm(c["text"])).ratio()
        if r > score:
            best, score = c, r
    return best if score >= 0.8 else None


def answer(workdir: Path, result: dict, question: str, llm=None, chip: str | None = None, allow_ai: bool = True) -> dict:
    """The answer to one question about a finished run. A chip, or a typed question that clearly matches one, runs without an AI model.
    Free text goes to the model only when allow_ai is true. `llm` defaults to the configured model; tests pass a fake one."""
    question = " ".join(question.split())[:300]
    if not question and not chip:
        return {"status": "no_fit", "text": "Type a question about this report.", "suggestions": []}
    df, measures, groups, dates, values = _prepare(workdir, result)
    if not measures:
        return {"status": "no_fit", "text": "This data has no number column to ask about.", "suggestions": []}
    cs = chips(workdir, result)
    picked = next((c for c in cs if c["id"] == chip), None) if chip else match_chip(question, cs)
    if picked is not None:
        return _execute(workdir, df, picked["op"])
    suggestions = [c["text"] for c in cs[:2]] or _suggestions(measures, groups, dates)
    model = (llm if llm is not None else llmmod.get_llm()[0]) if allow_ai else None
    if model is None:
        return {"status": "unavailable", "text": "Without an AI model I can answer the suggested questions only. Pick one of these.", "suggestions": [c["text"] for c in cs]}
    system, user = _prompt(measures, groups, dates, values)
    choice, reason = llmmod.ask_json_ex(model, system, user.format(q=question), repair=True)
    if choice is None:
        return {"status": "unavailable", "text": "I could not read that question just now. Please try again in a moment.", "suggestions": suggestions}
    op, why = _check(choice, measures, groups, dates, values)
    if op is None:
        return {"status": "no_fit", "text": f"I can't answer that from this report: {why}. Here are two questions I can answer.", "suggestions": suggestions}
    return _execute(workdir, df, op)


def history(workdir: Path) -> list[dict]:
    import json
    p = workdir / "chat.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, ValueError):
        return []


def save_history(workdir: Path, items: list[dict]) -> None:
    import json
    (workdir / "chat.json").write_text(json.dumps(items[-50:]), encoding="utf-8")
