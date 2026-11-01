"""data_catalog: what is IN the data, answered by code. The AI proposes search terms for the user's concept (synonyms, local usage, spelling variants) and which lookup is needed; code searches the
column names, the display names, the understanding record's meanings and the actual values of the text columns (case-insensitive, with a fuzzy match), and answers with counts and shares it computed.
  find     "Yes: column X contains <value> in n rows (p%)", "Yes: the data has a column X ...", or "No column or value matches ...; the closest columns are A and B"
  columns  what columns the data has
  values   what values one column holds (at most 30 distinct values, with counts and shares), or its distinct count when the column may hold personal details or identifiers
  missing  how much of one column (or of every column) is missing
The AI never receives a row: this tool's results go to the person, and what the AI sees is only the data card."""
from __future__ import annotations

import difflib
import re

import pandas as pd

from ... import chips as chipmod
from ... import howtext, reports
from .. import datacard, verify
from ..registry import Tool, ToolResult, register
from .... import viewselect as vs

MAX_TERMS, MAX_VALUES, MAX_HITS = 12, 30, 12
FUZZY = 0.82


def _clean_terms(terms) -> list[str]:
    out = []
    for t in terms or []:
        if isinstance(t, str):
            t = " ".join(re.sub(r"[^\w\s&'-]", " ", t).split())[:60]
            if len(t) >= 2 and t.lower() not in [x.lower() for x in out]:
                out.append(t)
    return out[:MAX_TERMS]


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _word_hit(term: str, text: str) -> bool:
    """The term is, or is a spelling variant of, a word or a phrase of the text."""
    t, x = term.lower().strip(), text.lower().replace("_", " ")
    if len(t) >= 3 and (t in x or (len(x) >= 3 and x in t)):
        return True
    words = re.findall(r"[a-z0-9]+", x)
    return len(t) >= 4 and any(_sim(t, w) >= FUZZY for w in words + [" ".join(words[i:i + 2]) for i in range(len(words) - 1)])


def column_matches(ctx, terms: list[str]) -> list[dict]:
    """Columns whose name, display name or meaning matches a term. {col, term, via}."""
    out = []
    for c in ctx.cols.values():
        for t in terms:
            via = "name" if _word_hit(t, c.name) or _word_hit(t, c.display) else "meaning" if c.meaning and (_word_hit(t, c.meaning) or vs.words(t) and vs.words(t) <= vs.words(c.meaning)) else ""
            if via:
                out.append({"col": c, "term": t, "via": via})
                break
    return out


def value_matches(df: pd.DataFrame, ctx, terms: list[str]) -> list[dict]:
    """Values of text columns that match a term, with the number of rows. {col, term, value, rows, shown (what may be printed), personal}."""
    out = []
    n = len(df)
    for c in ctx.cols.values():
        if c.name not in df.columns or c.role in ("measure", "date", "free_text"):
            continue
        s = df[c.name]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
            continue
        low = s.dropna().astype(str).str.strip().str.lower()
        if low.empty or low.nunique() > 50000:
            continue
        vc = low.value_counts()
        uniq = list(vc.index)
        personal = bool(datacard.personal_looking(c.name, c.display, s.dropna(), n))
        for t in terms:
            tl = t.lower()
            hit = [v for v in uniq if v == tl or (len(tl) >= 4 and (tl in v or (len(v) >= 4 and v in tl)))]
            if not hit and len(tl) >= 4:
                hit = difflib.get_close_matches(tl, uniq[:20000], n=3, cutoff=FUZZY)
            for v in hit[:3]:
                out.append({"col": c, "term": t, "value": v, "rows": int(vc[v]), "personal": personal})
    return out


def closest_columns(ctx, terms: list[str], k: int = 2) -> list:
    scored = []
    for c in ctx.cols.values():
        best = 0.0
        for t in terms:
            best = max(best, _sim(t, c.display), _sim(t, c.name.replace("_", " ")), 0.9 * len(vs.words(t) & vs.words(c.meaning + " " + c.display)) / max(1, len(vs.words(t))))
        scored.append((best, c))
    return [c for s, c in sorted(scored, key=lambda x: -x[0])[:k] if s > 0.2]


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


class DataCatalog(Tool):
    name = "data_catalog"
    description = ("Answer what is IN the data from the column names, meanings and actual values. lookup=find with `terms`: words for the user's concept (the words they used, synonyms, local usage, spelling variants; up to 12); "
                   "lookup=columns lists the columns; lookup=values with `column` lists its values; lookup=missing (with `column`, or without for all) says how much is missing.")
    args = {"lookup": {"type": "str", "enum": ["find", "columns", "values", "missing"]}, "terms": {"type": "list", "max_len": MAX_TERMS}, "column": {"type": "str", "max_len": 80}}
    soft_validation = True

    def validate(self, args, env):
        lk = args.get("lookup") or "find"
        if lk == "find" and not _clean_terms(args.get("terms")):
            return ["Give the words to look for."]
        if lk == "values" and (not args.get("column") or env.ctx.find(args["column"]) is None):
            return [f"I don't have a column called “{str(args.get('column') or '')[:40]}”."]
        if lk == "missing" and args.get("column") and env.ctx.find(args["column"]) is None:
            return [f"I don't have a column called “{str(args['column'])[:40]}”."]
        return []

    # ------------------------------------------------------------------ answers
    def _answer(self, env, text: str, lines: list[str], figures: list[dict], rows: list[list], how_op: str, notes: list[str], cols=("Column", "Match", "Rows", "Share")) -> ToolResult:
        fid = None
        if figures or rows:
            fid, fact = verify.facts_from_table("k", env, list(cols), [[str(c) for c in r] for r in rows] or [[f["shown"]] for f in figures], text)
            env.facts[fid] = fact
        how = howtext.record_how(how_op, ["your column names, their meanings and the values in your data"], [], ["Every count and share was worked out by code on the whole cleaned table; the AI only suggested what to look for."] + notes)
        chips = chipmod.relevant(env.ctx, env.df, env.result, env.question, None, reports.prev_chip_ids(env.wd), 2)
        table = {"columns": list(cols), "rows": rows[:60], "shown": [[str(c) for c in r] for r in rows[:60]], "total_rows": len(rows), "truncated": len(rows) > 60} if rows else None
        ans, _ = reports._base("catalog", text, "About your data", how, chips, 0, lines=lines, figures=figures, fact_ids=[fid] if fid else [], **({"table": table} if table else {}))
        return ToolResult(summary=how_op, answer=ans, chips=chips, facts={fid: env.facts[fid]} if fid else {})

    def _columns(self, env) -> ToolResult:
        cs = [c for c in env.ctx.cols.values() if c.name in env.df.columns]
        rows = [[c.display, c.role, _pct(float(env.df[c.name].isna().mean()))] for c in cs]
        n = len(env.df)
        names = ", ".join(c.display for c in cs[:25]) + (f" and {len(cs) - 25} more" if len(cs) > 25 else "")
        figs = [{"label": "columns", "value": len(cs), "shown": f"{len(cs)}"}, {"label": "rows", "value": n, "shown": f"{n:,}"}]
        return self._answer(env, f"The data has {len(cs)} columns and {n:,} rows: {names}.", [], figs, rows, "Listed the columns of your data", [], cols=("Column", "Kind", "Missing"))

    def _values(self, env, column: str) -> ToolResult:
        c = env.ctx.find(column)
        s = env.df[c.name]
        nn = s.dropna()
        n = len(env.df)
        why = datacard.personal_looking(c.name, c.display, nn, n) if c.role not in ("measure", "date") else ""
        if c.role in ("id", "free_text") or why:
            reason = why or ("it is an identifier" if c.role == "id" else "it is free text")
            figs = [{"label": "distinct values", "value": int(nn.nunique()), "shown": f"{int(nn.nunique()):,}"}]
            return self._answer(env, f"I don't list the values of “{c.display}” because {reason}. It has {int(nn.nunique()):,} distinct values, and {_pct(float(s.isna().mean()))} of it is missing.", [], figs, [], f"Counted the distinct values of {c.display}", [])
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
            lo, hi = (nn.min(), nn.max()) if len(nn) else ("", "")
            rng = f"from {datacard._fmt(lo) if pd.api.types.is_numeric_dtype(s) else str(lo.date())} to {datacard._fmt(hi) if pd.api.types.is_numeric_dtype(s) else str(hi.date())}"
            return self._answer(env, f"“{c.display}” has {int(nn.nunique()):,} distinct values, {rng}; {_pct(float(s.isna().mean()))} of it is missing.", [], [{"label": "distinct values", "value": int(nn.nunique()), "shown": f"{int(nn.nunique()):,}"}], [], f"Described the range of {c.display}", [])
        vc = nn.astype(str).value_counts()
        top = vc.head(MAX_VALUES)
        rows = [[v, f"{k:,}", _pct(k / n)] for v, k in top.items()]
        more = f" Only the {MAX_VALUES} most common are shown." if len(vc) > MAX_VALUES else ""
        figs = [{"label": "distinct values", "value": int(vc.size), "shown": f"{int(vc.size):,}"}]
        return self._answer(env, f"“{c.display}” holds {int(vc.size):,} distinct values; the most common is {top.index[0]} ({int(top.iloc[0]):,} rows, {_pct(top.iloc[0] / n)}).{more}", [], figs, rows, f"Listed the values of {c.display}", [], cols=("Value", "Rows", "Share"))

    def _missing(self, env, column: str | None) -> ToolResult:
        n = len(env.df)
        if column:
            c = env.ctx.find(column)
            k = int(env.df[c.name].isna().sum())
            return self._answer(env, f"{_pct(k / n if n else 0)} of “{c.display}” is missing ({k:,} of {n:,} rows).", [], [{"label": "rows missing", "value": k, "shown": f"{k:,}"}], [[c.display, f"{k:,}", _pct(k / n if n else 0)]], f"Counted the missing values of {c.display}", [], cols=("Column", "Rows missing", "Share"))
        miss = sorted(((int(env.df[c.name].isna().sum()), c) for c in env.ctx.cols.values() if c.name in env.df.columns), key=lambda x: -x[0])
        miss = [(k, c) for k, c in miss if k > 0]
        if not miss:
            return self._answer(env, "No column has any missing values.", [], [{"label": "columns with missing values", "value": 0, "shown": "0"}], [], "Counted the missing values of every column", [])
        rows = [[c.display, f"{k:,}", _pct(k / n)] for k, c in miss[:20]]
        return self._answer(env, f"{len(miss)} of the columns have missing values; the most is in “{miss[0][1].display}” ({_pct(miss[0][0] / n)}).", [], [{"label": "columns with missing values", "value": len(miss), "shown": str(len(miss))}], rows, "Counted the missing values of every column", [], cols=("Column", "Rows missing", "Share"))

    def _find(self, env, terms: list[str]) -> ToolResult:
        n = len(env.df)
        cm = column_matches(env.ctx, terms)
        vm = value_matches(env.df, env.ctx, terms)
        lines, rows, figs = [], [], []
        by_col: dict[tuple, list] = {}
        for h in vm:
            by_col.setdefault((h["col"].name, h["term"]), []).append(h)
        for (_, term), hs in by_col.items():
            c = hs[0]["col"]
            if hs[0]["personal"]:
                total = sum(h["rows"] for h in hs)
                lines.append(f"Yes: column “{c.display}” has a value that matches “{term}” in {total:,} rows ({_pct(total / n)}).")
                rows.append([c.display, f"matches “{term}”", f"{total:,}", _pct(total / n)])
                figs.append({"label": f"rows matching {term}", "value": total, "shown": f"{total:,}"})
            else:
                part = "; ".join(f"“{h['value']}” in {h['rows']:,} rows ({_pct(h['rows'] / n)})" for h in hs)
                lines.append(f"Yes: column “{c.display}” contains {part}.")
                for h in hs:
                    rows.append([c.display, f"value “{h['value']}”", f"{h['rows']:,}", _pct(h["rows"] / n)])
                    figs.append({"label": f"rows with {h['value']}", "value": h["rows"], "shown": f"{h['rows']:,}"})
        seen_cols = {k[0] for k in by_col}
        for m in cm:
            c = m["col"]
            if c.name in seen_cols or c.name not in env.df.columns:
                continue
            miss = float(env.df[c.name].isna().mean())
            extra = " It is mostly empty, so it tells you little." if miss >= 0.5 else ""
            lines.append(f"Yes: the data has a column “{c.display}”" + (f" ({c.meaning})" if c.meaning else "") + f"; {_pct(miss)} of its values are missing.{extra}")
            rows.append([c.display, f"column ({m['via']})", f"{int(round((1 - miss) * n)):,}", _pct(1 - miss)])
            figs.append({"label": f"rows filled in {c.display}", "value": int(round((1 - miss) * n)), "shown": f"{int(round((1 - miss) * n)):,}"})
        quoted = ", ".join(f"“{t}”" for t in terms[:6])
        if not lines:
            close = closest_columns(env.ctx, terms)
            tail = (" The closest columns are " + " and ".join(f"“{c.display}”" for c in close) + ".") if close else " No column is close either."
            text = f"No: no column or value matches {quoted}.{tail}"
            return self._answer(env, text, [], [], [], "Searched your column names, meanings and values", [f"Looked for: {quoted}, and spelling variants."])
        lines = lines[:MAX_HITS]
        return self._answer(env, " ".join(lines), lines, figs[:MAX_HITS * 3], rows[:60], "Searched your column names, meanings and values", [f"Looked for: {quoted}, and spelling variants."])

    def run(self, args, env, prior):
        lk = args.get("lookup") or "find"
        if lk == "columns":
            return self._columns(env)
        if lk == "values":
            return self._values(env, args["column"])
        if lk == "missing":
            return self._missing(env, args.get("column"))
        return self._find(env, _clean_terms(args.get("terms")))


register(DataCatalog)
