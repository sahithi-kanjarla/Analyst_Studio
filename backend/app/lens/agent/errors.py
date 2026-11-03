"""What to do when a step fails. A failure is never shown as "failed unexpectedly": each kind of failure is mapped to a plain sentence of what happened and what to try, and the failing step is written
to lens_errors.jsonl in the run folder (the tool, the kind, the error with paths and long values removed, and the SQL when there was one) so it can be debugged afterwards."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import sqlguard

FILE = "lens_errors.jsonl"
TRY = " Try asking it another way, or a simpler version of it."


def kind_of(e: BaseException) -> str:
    mod = type(e).__module__ or ""
    if isinstance(e, sqlguard.SqlRejected):
        return "query"
    if mod.startswith("duckdb") or mod.startswith("sqlglot"):
        return "database"
    if isinstance(e, TimeoutError):
        return "timeout"
    if isinstance(e, MemoryError):
        return "memory"
    if isinstance(e, OSError):
        return "file"
    if isinstance(e, (KeyError, IndexError, ValueError, TypeError, AttributeError, ZeroDivisionError, OverflowError, ArithmeticError)):
        return "result"
    return "other"


def plain_message(e: BaseException) -> str:
    """A sentence for the person: what happened, and what to try."""
    k = kind_of(e)
    if k == "query":
        return str(e)
    return {"database": "The calculation could not be run on your data." + TRY, "timeout": "That took too long, so I stopped it." + TRY,
            "memory": "That needed more room than I am allowed to use, so I stopped it. Try a smaller question, for example for one group or one period.",
            "file": "Something the answer needed could not be read." + " Please try again in a moment.",
            "result": "I could not put that result together from your data." + TRY}.get(k, "Something went wrong while I was working on that, so I stopped." + TRY)


def log(wd, tool: str, e: BaseException | str, sql: str | None = None) -> None:
    """One line per failed step, for debugging. The error text has paths and long values removed; the SQL is the query as the AI wrote it (it holds no data)."""
    try:
        line = {"at": time.time(), "tool": tool, "kind": kind_of(e) if isinstance(e, BaseException) else "text", "error": sqlguard.sanitise_error(f"{type(e).__name__}: {e}" if isinstance(e, BaseException) else e)}
        if sql:
            line["sql"] = str(sql)[:1500]
        with open(Path(wd) / FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
    except OSError:
        pass
