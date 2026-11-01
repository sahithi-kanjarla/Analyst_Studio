"""custom_analysis: for requests the closed operations cannot express. The AI writes ONE SQL query over the registered view `data` (the cleaned table); code parses and validates it (`sqlguard`: one SELECT,
only that view, only known columns, no DDL, DML, file or network function), runs it in DuckDB with a timeout, a memory cap and row caps, and checks the result. The AI never receives rows.
On a validation or execution error the AI gets the sanitised error and may repair the query up to 2 times (these are the 2 extra calls this tool may use). The result becomes a fact with an id
that a reply or a brief can cite. When a closed operation is also given as `check_query`, it is run too and the two are compared: a disagreement is shown, not hidden.
Presentation: labelled "Custom analysis"; the box "How this was calculated" has a plain description and, collapsed, the SQL."""
from __future__ import annotations

import json
import re

from .... import llm as llmmod
from ... import context as ctxmod
from ... import engine, howtext, query as qmod, reports
from .. import errors, sqlguard, verify
from ..registry import Tool, ToolResult, register

MAX_REPAIRS = 2
REPAIR_SYSTEM = """You fix ONE SQL query for DuckDB. The only table is "data". Use only the columns listed (exactly as written). Reply with one JSON object: {"sql":"<one SELECT or WITH ... SELECT>","description":"<one plain sentence saying what it computes, no numbers>"}.
Only SELECT is allowed: no other statement, no file, network or system function. Return the JSON object only."""


def schema_lines(env) -> list[dict]:
    return [{"column": c.name, "label": c.display, "role": c.role, "kind": c.kind} for c in env.ctx.cols.values()]


def matching_values(env, limit: int = 6) -> dict[str, list[str]]:
    """Real stored values of category columns for the words the user used, by the existing value matching. {column label: [values]}."""
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9&'.-]*", env.question) if len(w) >= 3]
    grams = list(dict.fromkeys(words + [" ".join(p) for p in zip(words, words[1:])]))
    out: dict[str, list[str]] = {}
    for c in env.ctx.groupable():
        try:
            if c.name not in env.df.columns or env.df[c.name].nunique(dropna=True) > 500:
                continue
            found: list[str] = []
            for g in grams:
                for v in qmod.resolve_value(env.df, c.name, g):
                    if v not in found:
                        found.append(v)
        except Exception:               # noqa: BLE001  a column that cannot be searched (nested values, odd types) is left out, never an error
            continue
        if found:
            out[c.display] = found[:limit]
    return out


def _readable(name: str, env) -> str:
    c = env.ctx.cols.get(name)
    if c:
        return c.display
    return re.sub(r"\s+", " ", re.sub(r"_+", " ", str(name))).strip().capitalize()


def _fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return ctxmod.fmt_num(v)
    return str(v)


def _num_columns(rows: list[list]) -> list[list[float]]:
    """The numeric columns of a result, each as its list of values."""
    cols = []
    for j in range(len(rows[0]) if rows else 0):
        vals = [r[j] for r in rows]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals if v is not None) and any(v is not None for v in vals):
            cols.append([float(v) for v in vals if v is not None])
    return cols


def _same_values(a: list[float], b: list[float]) -> bool:
    """Every value of `a` appears in `b` (within rounding)."""
    return all(any(abs(x - y) <= max(0.0051, 1e-6 * abs(x)) for y in b) for x in a)


def _agree(closed: list[list[float]], custom: list[list[float]]) -> bool:
    """The closed operation's first (main) numeric column is found, value for value, in one numeric column of the custom result."""
    return bool(closed) and any(_same_values(closed[0], c) for c in custom)


class CustomAnalysis(Tool):
    name = "custom_analysis"
    description = ("For a question the closed operations cannot express. ONE SQL SELECT over the table \"data\" (columns in SQL VIEW), plus a plain description of what it computes. "
                   "Optionally check_query: a data_query query that computes the same thing, to cross-check.")
    args = {"sql": {"type": "str", "required": True, "max_len": 4000, "help": "one SELECT or WITH ... SELECT over table data, columns exactly as listed"},
            "description": {"type": "str", "required": True, "max_len": 300, "help": "one plain sentence saying what it computes, no numbers"},
            "check_query": {"type": "dict", "help": "optional closed-operation query that computes the same thing"}}
    needs_ai = True
    soft_validation = True

    def validate(self, args, env):
        try:
            sqlguard.validate(args["sql"], list(env.df.columns))
        except sqlguard.SqlRejected:
            return []          # a rejected query is repaired inside run (with the sanitised error), not by re-planning
        return []

    # ------------------------------------------------------------------ the repair call
    def _repair(self, env, sql: str, err: str) -> tuple[str, str] | None:
        if not env.can_call(extra=True):
            return None
        user = ("COLUMNS (table data): " + json.dumps(schema_lines(env), ensure_ascii=False) + "\nMATCHING VALUES: " + json.dumps(matching_values(env), ensure_ascii=False)
                + f"\nREQUEST: {env.question}\nPREVIOUS SQL: {sql[:1500]}\nERROR: {sqlguard.sanitise_error(err)}")
        env.spend(1)
        obj, why = llmmod.ask_json_ex(llmmod.deterministic(env.llm), REPAIR_SYSTEM, user, repair=False)
        if not obj or not isinstance(obj.get("sql"), str):
            return None
        return obj["sql"], str(obj.get("description") or "")

    def run(self, args, env, prior):
        if not env.ai_ok:
            return ToolResult(ok=False, summary="Custom analysis was not available", error="Custom analysis needs the AI model, which is not available right now. The suggested questions still work.")
        sql, desc = args["sql"], " ".join(str(args.get("description") or "").split()) or "a custom calculation on your cleaned data"
        cols = list(env.df.columns)
        repairs, res, last = 0, None, ""
        while True:
            env.last_sql = sql
            try:
                checked = sqlguard.validate(sql, cols)
                res = sqlguard.execute(checked, env.df)
                bad = sqlguard.sanity(res)
                if bad:
                    raise sqlguard.SqlRejected(bad[0])
                break
            except sqlguard.SqlRejected as e:
                last = str(e)
                errors.log(env.wd, self.name, last, sql)                       # every refused or failed query is kept for debugging, with the reason
                fixed = self._repair(env, sql, last) if repairs < MAX_REPAIRS else None
                if not fixed:
                    return self._failed(env, last, repairs)
                sql, desc = fixed[0], (" ".join(fixed[1].split()) or desc)
                repairs += 1
        res.rows = [[v if v is None or isinstance(v, (int, float, str, bool)) else str(v) for v in r] for r in res.rows]       # whatever the database returns (dates, durations, decimals, lists) is shown as text
        columns = [_readable(c, env) for c in res.columns]
        shown = [[_fmt(v) for v in r] for r in res.rows[:sqlguard.SHOW_ROWS]]
        fid, fact = verify.facts_from_table("c", env, columns, [[_fmt(v) for v in r] for r in res.rows], desc)
        table = {"columns": columns, "rows": res.rows[:sqlguard.SHOW_ROWS], "shown": shown, "total_rows": len(res.rows), "truncated": res.truncated or len(res.rows) > sqlguard.SHOW_ROWS}
        figures = [{"label": columns[j], "value": v, "shown": shown[0][j]} for j, v in enumerate(res.rows[0]) if isinstance(v, (int, float)) and not isinstance(v, bool)] if len(res.rows) == 1 else []
        if len(res.rows) == 1:
            body = "; ".join(f"{columns[j]} {shown[0][j]}" for j in range(len(columns)))
            text = f"{desc.rstrip('.')}: {body}."
        else:
            text = f"{desc.rstrip('.')}. The result has {len(res.rows):,}{'+' if res.truncated else ''} row{'s' if len(res.rows) != 1 else ''}; the table shows {len(shown)}."
        notes = [f"Result: {len(res.rows):,} row{'s' if len(res.rows) != 1 else ''}, read in {res.seconds:.2f} s from a read-only view of your cleaned table.",
                 "The AI wrote the query; code checked it (one SELECT, only your columns, no files or network), ran it with a time and memory limit, and checked the result."]
        sn = sqlguard.share_note(columns, res.rows)
        if sn:
            notes.append(sn)
        if repairs:
            notes.append(f"The first query was corrected {repairs} time{'s' if repairs != 1 else ''} after an error.")
        if not res.aggregated:
            notes.append(f"This result is not aggregated, so only the first {sqlguard.RAW_ROWS} rows are shown, to you only; the AI never receives rows.")
        extra = {}
        cq = args.get("check_query")
        if isinstance(cq, dict):
            text, extra, notes = self._cross_check(env, cq, res, text, notes)
        how = howtext.with_lines({"operation": f"Custom analysis: {desc.rstrip('.')}", "sources": ["a read-only view of your cleaned table"], "fact_ids": [fid], "notes": notes, "sql": checked.sql,
                                  "plain": desc})
        from ... import chips as chipmod
        nxt = chipmod.relevant(env.ctx, env.df, env.result, env.question, None, reports.prev_chip_ids(env.wd), 2)
        ans, _ = reports._base("custom", text, "Custom analysis", how, nxt, 0, table=table, figures=figures, fact_ids=[fid], **extra)
        return ToolResult(summary="Ran a custom analysis on your cleaned data" + (" (query corrected)" if repairs else ""), answer=ans, chips=nxt, facts={fid: fact}, data={"result": res, "fact_id": fid, "description": desc, "sql": checked.sql})

    # ------------------------------------------------------------------ cross-check with a closed operation
    def _cross_check(self, env, cq: dict, res, text: str, notes: list[str]):
        ch = qmod.check(cq, env.ctx, env.df)
        if not ch.query:
            return text, {}, notes + ["The closed operation given for cross-checking could not be run, so no comparison was made."]
        try:
            R = engine.run(ch.query, env.df, env.ctx)
        except engine.Cannot:
            return text, {}, notes + ["The closed operation given for cross-checking could not be run, so no comparison was made."]
        a, b = _num_columns(R.rows), _num_columns(res.rows)
        if not a:
            return text, {}, notes + ["The closed operation returned no figures to compare."]
        if _agree(a, b):
            return text, {}, notes + ["A closed operation computed the same figures, so the two methods agree."]
        second = {"label": "Closed operation", "text": R.sentence, "table": {"columns": R.columns, "rows": R.rows[:sqlguard.SHOW_ROWS], "shown": R.shown[:sqlguard.SHOW_ROWS]}}
        return ("Two methods disagree. The custom analysis says: " + text + " The closed operation says: " + R.sentence), {"second_method": second, "disagree": True}, \
            notes + ["The two methods gave different figures. Both are shown; treat the result as unconfirmed."]

    def _failed(self, env, err: str, repairs: int) -> ToolResult:
        from ... import chat
        ans, nxt = chat._plain("no_fit", "I couldn't answer that reliably with a custom analysis" + (f" (I tried {repairs + 1} times)" if repairs else "") + ". Here are the closest questions I can answer.", env.wd, env.ctx, env.df, env.result, 0, env.question)
        return ToolResult(ok=False, summary="The custom analysis could not be completed", answer=ans, chips=nxt, error="The custom analysis could not be completed reliably.")


register(CustomAnalysis)
