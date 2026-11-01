"""Validating and running the one SQL query a custom analysis may use.

Validation is by PARSING (sqlglot), never by looking for words in the text: exactly one statement, a SELECT (or WITH ... SELECT, or a UNION of them), reading only the registered view and CTEs defined in the
query, using only known columns, with no DDL, DML, PRAGMA, ATTACH, COPY, INSTALL, LOAD, SET, table function, file or network function, and nothing on the deny list. What runs is the SQL regenerated from
the parsed tree, not the text the AI wrote, so a comment or an odd spelling cannot carry anything through.

Execution is DuckDB in memory over a read-only view of the cleaned table, with external access switched off and the configuration locked, a memory cap, a 10 s timeout (the query is interrupted) and a
cap on the rows read. The AI never receives rows: aggregated results passed on are capped at 50 rows and 12 columns, and a query that is not aggregated is capped at 20 rows and is for the user only."""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field

import pandas as pd
import logging

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)

VIEW = "data"
TIMEOUT_S = 10.0
MEMORY_LIMIT = "512MB"
MAX_READ_ROWS = 2000            # the most rows ever fetched from DuckDB
AGG_ROWS_TO_AI, AGG_COLS_TO_AI = 50, 12
RAW_ROWS = 20                   # a query that is not aggregated
SHOW_ROWS = 60                  # the most rows shown to the user
MAX_SQL_CHARS = 4000

DENY_FUNCS = {"glob", "getenv", "current_setting", "query", "query_table", "sniff_csv", "load_extension", "install_extension", "system", "shell", "pg_sleep", "sleep", "range_sleep",
              "current_database", "current_schema", "version", "pragma_version", "random_seed", "setseed", "which_secret", "read_text", "read_blob", "write_csv", "copy_to"}
DENY_PREFIX = ("read_", "write_", "duckdb_", "pragma_", "http", "s3", "gcs", "azure", "iceberg", "delta", "sqlite", "postgres", "mysql", "arrow", "parquet", "csv", "json_", "icu_", "from_", "scan_", "query_", "glob_",
               "list_files", "ls", "system_")
DENY_TEXT = re.compile(r"\b(?:attach|detach|pragma|install|load|copy|export|import|call|checkpoint|vacuum|create|drop|alter|insert|update|delete|replace|truncate|merge|set|reset|use|describe|show|summarize|explain|begin|commit|rollback|grant|revoke)\b", re.I)
BAD_NODES = tuple(t for t in (getattr(exp, n, None) for n in (
    "Create", "Insert", "Update", "Delete", "Drop", "Alter", "Copy", "Command", "Pragma", "Set", "Use", "Merge", "Transaction", "Commit", "Rollback", "Attach", "Detach", "LoadData", "Describe",
    "TruncateTable", "Grant", "Show", "Analyze", "Kill", "Refresh", "SetItem", "Export", "Summarize", "Install", "Declare")) if t is not None)


class SqlRejected(Exception):
    """The query is not allowed. The message is a plain sentence that is safe to show and to give back to the AI."""


@dataclass
class Checked:
    sql: str                 # the regenerated SQL that is run
    aggregated: bool
    tables: list[str] = field(default_factory=list)


def sanitise_error(e: Exception | str) -> str:
    """An error text that is safe to send to the AI: no paths, no stack, no values from the data, at most 300 characters."""
    t = " ".join(str(e).split())
    t = re.sub(r"(?:/|[A-Za-z]:\\)[\w./\\-]+", "<path>", t)
    t = re.sub(r"\bLINE \d+:.*", "", t)
    t = re.sub(r"'[^']{12,}'", "'…'", t)
    return t[:300]


def _is_agg(select: exp.Expression) -> bool:
    if select.args.get("group"):
        return True
    for e in select.args.get("expressions") or []:
        for n in e.walk():
            if isinstance(n, exp.AggFunc) and not n.find_ancestor(exp.Window):
                return True
    return False


def validate(sql: str, columns: list[str], view: str = VIEW) -> Checked:
    """Parses and checks the query; returns what to run. Raises SqlRejected with a plain sentence when anything is not allowed."""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlRejected("The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise SqlRejected(f"The query is longer than {MAX_SQL_CHARS} characters.")
    if "\x00" in sql:
        raise SqlRejected("The query contains a character that is not allowed.")
    try:
        stmts = sqlglot.parse(sql, read="duckdb")
    except Exception as e:                                   # sqlglot's ParseError / TokenError
        raise SqlRejected("The query could not be read as SQL: " + sanitise_error(str(e).splitlines()[0] if str(e) else "syntax error")) from None
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        raise SqlRejected("Only one SELECT statement is allowed; the query has " + ("none" if not stmts else f"{len(stmts)} statements") + ".")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)) or (isinstance(tree, exp.Subquery) and not isinstance(tree.unnest(), (exp.Select, exp.Union))):
        raise SqlRejected("Only a SELECT (or WITH ... SELECT) query is allowed.")
    for n in tree.walk():
        if isinstance(n, BAD_NODES):
            raise SqlRejected(f"{type(n).__name__.upper()} statements are not allowed; only reading with SELECT.")
        if isinstance(n, (exp.Into,)):
            raise SqlRejected("SELECT ... INTO is not allowed.")
        if isinstance(n, exp.Anonymous) or isinstance(n, exp.Func):
            name = (n.name if isinstance(n, exp.Anonymous) else n.sql_name()).lower()
            if name in DENY_FUNCS or name.startswith(DENY_PREFIX):
                raise SqlRejected(f"The function {name} is not allowed (no file, network or system functions).")
        if isinstance(n, (exp.Lateral, exp.Unnest, exp.Values)) and isinstance(n.parent, exp.From) and isinstance(n, exp.Table):
            raise SqlRejected("Table functions are not allowed.")
        if isinstance(n, exp.Table) and not isinstance(n.this, (exp.Identifier, type(None))):
            raise SqlRejected("Only the registered table can be read, not a file, a function or a value list.")
        if isinstance(n, exp.Parameter) or isinstance(n, exp.Placeholder):
            raise SqlRejected("Parameters are not allowed.")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias} | {t.alias.lower() for t in tree.find_all(exp.TableAlias) if t.alias}
    aliases |= {c.name.lower() for ta in tree.find_all(exp.TableAlias) for c in (ta.args.get("columns") or [])}
    tables = []
    for t in tree.find_all(exp.Table):
        nm = (t.name or "").lower()
        if t.args.get("db") or t.args.get("catalog"):
            raise SqlRejected("Only the registered table can be read; no schema or database names.")
        if nm in ctes:
            continue
        if nm != view.lower():
            raise SqlRejected(f"The table “{t.name[:40]}” is not available. The only table is “{view}”.")
        tables.append(nm)
    if not tables:
        raise SqlRejected(f"The query must read the table “{view}”.")
    known = {c.lower() for c in columns}
    table_names = {view.lower(), *ctes} | {t.alias.lower() for t in tree.find_all(exp.Table) if t.alias} | {a for a in aliases}
    for c in tree.find_all(exp.Column):
        if isinstance(c.this, exp.Star):
            continue
        nm = (c.name or "").lower()
        if c.table and c.table.lower() not in table_names:
            raise SqlRejected(f"“{c.table[:40]}” is not a table or alias in this query.")
        if nm not in known and nm not in aliases:
            raise SqlRejected(f"There is no column called “{c.name[:40]}”. Use only the columns listed.")
    for s in tree.find_all(exp.Star):
        pass
    try:
        out = tree.sql(dialect="duckdb", comments=False)
    except Exception as e:
        raise SqlRejected("The query could not be rewritten safely: " + sanitise_error(e)) from None
    if re.search(r";", out):
        raise SqlRejected("Only one statement is allowed.")
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    agg = bool(sel is not None and _is_agg(sel)) and (isinstance(tree, exp.Select) or all(_is_agg(s) for s in tree.find_all(exp.Select) if s.parent is None or True))
    return Checked(out, agg, tables)


@dataclass
class Result:
    columns: list[str]
    rows: list[list]
    aggregated: bool
    truncated: bool
    seconds: float = 0.0
    not_numbers: int = 0         # NaN or infinite values the database returned (shown as empty)


def _is_bad(v) -> bool:
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            return str(v)
    if isinstance(v, (pd.Timestamp,)):
        return str(v.date()) if v.hour == 0 and v.minute == 0 else str(v)
    return v


def execute(checked: Checked, df: pd.DataFrame, view: str = VIEW) -> Result:
    """Runs the validated query in a fresh in-memory DuckDB. Raises SqlRejected for a timeout or a database error, with a sanitised message."""
    import time

    import duckdb
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
        con.execute("SET threads=2")
        con.register(view, df)
        con.execute("SET enable_external_access=false")
        con.execute("SET autoinstall_known_extensions=false")
        con.execute("SET autoload_known_extensions=false")
        con.execute("SET lock_configuration=true")
        cap = MAX_READ_ROWS if checked.aggregated else RAW_ROWS
        sql = f"SELECT * FROM ({checked.sql}) AS _q LIMIT {cap + 1}"
        box: dict = {}

        def work():
            try:
                cur = con.execute(sql)
                box["cols"] = [d[0] for d in cur.description]
                box["rows"] = cur.fetchmany(cap + 1)
            except Exception as e:           # noqa: BLE001
                box["err"] = e
        t0 = time.monotonic()
        th = threading.Thread(target=work, daemon=True)
        th.start()
        th.join(TIMEOUT_S)
        if th.is_alive():
            try:
                con.interrupt()
            except Exception:
                pass
            th.join(2)
            raise SqlRejected(f"The query took longer than {int(TIMEOUT_S)} seconds and was stopped.")
        if "err" in box:
            raise SqlRejected("The query failed: " + sanitise_error(box["err"]))
        bad = sum(1 for r in box["rows"] for v in r if _is_bad(v))
        rows = [[_clean(v) for v in r] for r in box["rows"]]
        truncated = len(rows) > cap
        return Result(box["cols"], rows[:cap], checked.aggregated, truncated, time.monotonic() - t0, bad)
    finally:
        try:
            con.close()
        except Exception:
            pass


def sanity(res: Result) -> list[str]:
    """Problems that make a result unreliable: empty, NaN or infinite values."""
    if not res.rows:
        return ["The query returned no rows."]
    if res.not_numbers:
        return [f"The result has {res.not_numbers} value{'s' if res.not_numbers != 1 else ''} that are not numbers (for example from a division by zero)."]
    bad = 0
    for r in res.rows:
        for v in r:
            if v is None:
                bad += 1
    if bad and all(v is None for r in res.rows for v in r):
        return ["Every value in the result is empty."]
    return []


def for_ai(res: Result) -> dict | None:
    """What may be passed on to the AI: an aggregated result, at most 50 rows and 12 columns. None for a query that is not aggregated (those rows are for the user only)."""
    if not res.aggregated:
        return None
    return {"columns": res.columns[:AGG_COLS_TO_AI], "rows": [r[:AGG_COLS_TO_AI] for r in res.rows[:AGG_ROWS_TO_AI]]}


SHARE_WORDS = re.compile(r"share|percent|pct|proportion|ratio_of_total", re.I)


def share_note(columns: list[str], rows: list[list]) -> str:
    """When a column looks like shares of a whole and its parts do not add up to the whole, a sentence saying by how much (the parts may legitimately be a subset); '' when they do or it is not shares."""
    if len(rows) < 2:
        return ""
    for j, c in enumerate(columns):
        if not SHARE_WORDS.search(str(c)):
            continue
        vals = [r[j] for r in rows if isinstance(r[j], (int, float)) and not isinstance(r[j], bool)]
        if len(vals) != len(rows) or any(v < 0 for v in vals):
            continue
        tot = sum(vals)
        whole = 1.0 if max(vals) <= 1.0 else 100.0
        if abs(tot - whole) > 0.02 * whole:
            return f"The {c} values add up to {tot:,.4g}, not {whole:g}, so they may cover only part of the whole."
    return ""
