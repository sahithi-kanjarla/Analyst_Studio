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


def enabled() -> bool:
    return os.environ.get("CHATBOT", "off").strip().lower() in ("on", "1", "true", "yes")


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
        ax.plot([str(p) for p in s.index], s.values, color="#a4461f", linewidth=2)
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    elif op["op"] == "top_n":
        s = df.groupby(op["group"])[m].sum().sort_values(ascending=False)
        head = s.head(op["n"])
        rows = [[str(k), float(v), float(v) / total * 100 if total else 0.0] for k, v in head.items()]
        text = f"Top {op['n']} {op['group']} by total {m}: " + ", ".join(f"{k} {_money(v)} ({p:.1f}%)" for k, v, p in rows) + "."
        ax.barh([str(r[0]) for r in rows][::-1], [r[1] for r in rows][::-1], color="#a4461f")
    elif op["op"] == "compare":
        s = df[df[op["group"]] == op["a"]][m].sum(), df[df[op["group"]] == op["b"]][m].sum()
        rows = [[op["a"], float(s[0])], [op["b"], float(s[1])]]
        diff = (s[0] - s[1]) / s[1] * 100 if s[1] else 0.0
        text = f"Total {m}: {op['a']} {_money(s[0])} against {op['b']} {_money(s[1])}, {op['a']} is {abs(diff):.1f}% {'higher' if diff >= 0 else 'lower'}."
        ax.bar([op["a"], op["b"]], [s[0], s[1]], color=["#a4461f", "#8c867a"])
    elif op["op"] == "share":
        part = float(df[df[op["group"]] == op["value"]][m].sum())
        share = part / total * 100 if total else 0.0
        rows = [[op["value"], part, share]]
        text = f"{op['value']} accounts for {_money(part)} of the total {m} ({share:.1f}%)."
        ax.bar([op["value"], "all other"], [part, total - part], color=["#a4461f", "#8c867a"])
    else:  # filter_total
        sel = df[df[op["column"]] == op["value"]]
        part = float(sel[m].sum())
        rows = [[op["value"], part, len(sel)]]
        text = f"For {op['column']} = {op['value']}: {len(sel):,} rows with a total {m} of {_money(part)}."
        ax.bar([str(op["value"])], [part], color="#a4461f")
    ax.set_title(m if op["op"] != "trend" else f"{m} over time", fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(chart_path, dpi=110, facecolor="#ffffff")
    return {"text": text, "rows": rows, "figures": {"total": total}}


def answer(workdir: Path, result: dict, question: str, llm=None) -> dict:
    """The answer to one question about a finished run. `llm` defaults to the configured model; tests pass a fake one."""
    question = " ".join(question.split())[:300]
    if not question:
        return {"status": "no_fit", "text": "Type a question about this report.", "suggestions": []}
    df = pd.read_pickle(workdir / "clean.pkl")
    measures, groups, dates = _columns(result, df)
    values = {g: sorted(str(v) for v in df[g].dropna().unique()) for g in groups}
    suggestions = _suggestions(measures, groups, dates)
    if not measures:
        return {"status": "no_fit", "text": "This data has no number column to ask about.", "suggestions": []}
    model = llm if llm is not None else llmmod.get_llm()[0]
    if model is None:
        return {"status": "unavailable", "text": "Questions need an AI model. None is configured on this server, so no question can be answered.", "suggestions": []}
    system, user = _prompt(measures, groups, dates, values)
    choice, reason = llmmod.ask_json_ex(model, system, user.format(q=question), repair=True)
    if choice is None:
        return {"status": "unavailable", "text": f"The AI model could not read the question ({reason or 'no answer'}). Try again in a moment.", "suggestions": []}
    op, why = _check(choice, measures, groups, dates, values)
    if op is None:
        return {"status": "no_fit", "text": f"That question does not fit what this report can compute: {why}. Try one of these instead.", "suggestions": suggestions}
    key = hashlib.sha1(repr(sorted(op.items())).encode()).hexdigest()[:12]
    chart = workdir / "charts" / f"ask_{key}.png"
    chart.parent.mkdir(exist_ok=True)
    out = _run(op, df, chart)
    return {"status": "answered", "op": op["op"], "text": out["text"], "rows": out["rows"], "chart": f"charts/{chart.name}", "suggestions": []}
