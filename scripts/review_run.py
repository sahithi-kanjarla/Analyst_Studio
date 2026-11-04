"""Review run: runs the whole pipeline on the review pack (sample_data/review), one dataset at a time, and writes a plain review file per dataset to review_out/.

    python scripts/review_run.py --list                       # every dataset, whether it is done, and the command to run it
    python scripts/review_run.py --only R01                   # one dataset (real AI; needs the key in .env)
    python scripts/review_run.py --only R01 M01 --max-tokens 60000
    python scripts/review_run.py --only M03 --force --screenshots
    python scripts/review_run.py --fake-ai --only R01 M01     # dry run: fake model, no network, no key; writes to review_out/_dryrun

Options: --only NAME... (an id such as R01, a folder name, or a prefix such as M), --max-tokens N (stop cleanly when the budget is reached: the dataset in progress finishes its
remaining stages with rules only, is saved as PARTIAL, and no later dataset is started), --force (rerun a dataset already done; without it a done dataset is skipped),
--screenshots (one screenshot per tab at 1440 px wide into review_out/<dataset>/, one browser at a time), --out DIR, --goal TEXT.

What it writes (review_out/ is git-ignored):
    <folder>.md            the review of one dataset
    <folder>/record.json   the same facts for machines (a dataset is "done" when this says complete)
    summary.md             all datasets, latest run of each (rewritten every time)
    scoreboard.md / .csv   one row per run of a dataset, appended, never overwritten

Test tooling only. It imports the app and changes nothing in it. Timing is recorded by wrapping the stage functions, the pacing wait and the rate-limit sleep while this script runs;
the wrappers call the originals unchanged, so no behaviour and no figure differs. Every figure is compared with pandas (expected.json); where a check is only a search of the visible text
it says so. "Own compute" is stage time minus the time waiting for the AI to answer minus the time waiting for pacing or a rate-limit retry."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import functools
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "sample_data" / "review"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

GOAL_DEFAULT = "What stands out in this data, and what should I do about it?"
LENS_READER = "my accountant"
NODES = ["profile", "relate", "brief", "understand", "clarify", "plan", "clean", "analyze", "critic", "insights", "strategy", "report", "powerbi"]
NAV = [("report", "Report", ["report"]), ("insights", "Insights", ["insights"]), ("strategy", "Ideas to try", ["strategy"]), ("dashboard", "Dashboard", ["dashboard"]),
       ("data", "Data", ["data", "model", "cleaning", "plan"])]
RUN_TIMEOUT_S = 60 * 45


# ============================================================================ datasets
def load_index() -> list[dict]:
    return json.loads((REVIEW / "index.json").read_text(encoding="utf-8"))["datasets"]


def pick(index: list[dict], names: list[str] | None) -> list[dict]:
    if not names:
        return index
    out, seen = [], set()
    for n in names:
        key = n.lower()
        hits = [d for d in index if d["id"].lower() == key or d["folder"].lower() == key] or [d for d in index if d["id"].lower().startswith(key) or d["folder"].lower().startswith(key)]
        if not hits:
            sys.exit(f"No dataset matches '{n}'. Try --list.")
        for d in hits:
            if d["id"] not in seen:
                seen.add(d["id"])
                out.append(d)
    return out


AUTH_MESSAGE = "AI key refused: nothing was tested with the real AI"


def ai_mode(fake: bool, stages: dict, unavailable_count: int) -> str:
    """dry-run (fake model) · real (every AI call answered) · partly (some calls or stages unavailable) · rules-only (no AI call was answered). Only "real" runs count in the real-AI percentages."""
    if fake:
        return "dry-run"
    answered = sum(s["calls"] - s["failed"] for s in stages.values())
    if answered == 0:
        return "rules-only"
    failed = sum(s["failed"] for s in stages.values())
    return "partly" if failed or unavailable_count else "real"


def counted_modes(out: Path) -> tuple[str, ...]:
    return ("dry-run",) if out.name == "_dryrun" else ("real",)


def record_path(out: Path, d: dict) -> Path:
    return out / d["folder"] / "record.json"


def done_status(out: Path, d: dict) -> str:
    p = record_path(out, d)
    if not p.exists():
        return "not run"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("status", "?")
    except (OSError, ValueError):
        return "unreadable"


# ============================================================================ timing (records only; calls the originals unchanged)
class Timing:
    """Where the seconds went. `scope()` marks one stage or one Lens question on the current thread; the wrapped pacing wait and rate-limit sleep add their seconds to the open scope."""
    def __init__(self) -> None:
        self.local = threading.local()
        self.stages: "OrderedDict[str, dict]" = OrderedDict()
        self.tokens_total = 0

    def add_limit(self, seconds: float) -> None:
        acc = getattr(self.local, "acc", None)
        if acc is not None:
            acc["limit"] += seconds

    def open(self) -> dict:
        from app import llm
        self.local.acc = {"limit": 0.0, "t0": time.monotonic(), "n0": len(llm.usage_calls())}
        return self.local.acc

    def close(self, acc: dict) -> dict:
        from app import llm
        total = time.monotonic() - acc["t0"]
        rows = llm.usage_calls()[acc["n0"]:]
        sent = [r for r in rows if not r["cache_hit"]]
        ai = sum(r["seconds"] for r in sent)
        tokens = sum(r["input_tokens"] + r["output_tokens"] for r in sent)
        self.local.acc = None
        self.tokens_total += tokens
        return {"total": total, "ai": ai, "limit": acc["limit"], "own": max(0.0, total - ai - acc["limit"]), "calls": len(sent), "failed": sum(1 for r in sent if not r["ok"]), "tokens": tokens, "auth": sum(1 for r in sent if not r["ok"] and r["reason"] == "auth")}

    def add_stage(self, name: str, m: dict) -> None:
        s = self.stages.setdefault(name, {"total": 0.0, "ai": 0.0, "limit": 0.0, "own": 0.0, "calls": 0, "failed": 0, "tokens": 0, "passes": 0})
        for k in ("total", "ai", "limit", "own", "calls", "failed", "tokens"):
            s[k] += m[k]
        s["passes"] += 1


class AIKeyRefused(Exception):
    """The provider answered "auth" (key missing, invalid or revoked). Stops the dataset at once instead of running every stage on rules."""


class Budget:
    def __init__(self, max_tokens: int | None) -> None:
        self.max, self.spent_before, self.hit_at, self.auth_failed = max_tokens, 0, "", False

    def used(self, timing: Timing) -> int:
        return self.spent_before + timing.tokens_total

    def exceeded(self, timing: Timing) -> bool:
        return self.max is not None and self.used(timing) >= self.max


def install(timing: Timing, budget: Budget) -> None:
    """Wraps the pacing wait, the rate-limit sleep and each stage function. Safe to call once per process."""
    from app import graph, llm, pacing
    orig_wait, orig_sleep = pacing.wait_for_room, llm._sleep

    def timed_wait(tokens):
        if any(not r["ok"] and r["reason"] == "auth" for r in llm.usage_calls()):      # the key was already refused once: no more calls, no more waiting
            budget.auth_failed = True
            raise AIKeyRefused("the AI provider refused the key")
        t = time.monotonic()
        try:
            return orig_wait(tokens)
        finally:
            timing.add_limit(time.monotonic() - t)

    def timed_sleep(seconds):
        t = time.monotonic()
        try:
            return orig_sleep(seconds)
        finally:
            timing.add_limit(time.monotonic() - t)

    pacing.wait_for_room, llm._sleep = timed_wait, timed_sleep

    def wrap(name, fn):
        @functools.wraps(fn)
        def run(state):
            if budget.exceeded(timing) and not getattr(llm._tls, "daily", False):
                llm._tls.daily = True                       # the app's own "no more AI today" switch: every later call answers at once and the stage uses its rules
                budget.hit_at = budget.hit_at or name
            acc = timing.open()
            try:
                out = fn(state)
            finally:
                m = timing.close(acc)
                timing.add_stage(name, m)
            if m["auth"]:
                budget.auth_failed = True
                raise AIKeyRefused(f"the AI provider refused the key in the {name} stage")
            return out
        return run

    for n in NODES:
        setattr(graph, f"{n}_node", wrap(n, getattr(graph, f"{n}_node")))
    graph.GRAPH = graph.build_graph()


# ============================================================================ small helpers
def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip() or "?"
    except Exception:       # noqa: BLE001
        return "?"


def norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def stem_of(name: str) -> str:
    from app.graph import _safe_name
    return _safe_name(Path(name).stem)


def find_col(df, name):
    for c in df.columns:
        if str(c) == name:
            return c
    n = norm(name)
    return next((c for c in df.columns if norm(c) == n), None)


def sentences(text: str, k: int = 2) -> str:
    # Strip markdown marks, but keep an underscore that sits inside a name (cost_amount stays cost_amount) and never join words.
    t = re.sub(r"[*`#>]|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", " ", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return " ".join(re.split(r"(?<=[.!?])\s+", t)[:k])


def fmt_s(x: float) -> str:
    return f"{x:,.1f}"


def md_table(head: list[str], rows: list[list]) -> str:
    if not rows:
        return "_none_\n"
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    return "| " + " | ".join(head) + " |\n|" + "|".join("---" for _ in head) + "|\n" + "\n".join("| " + " | ".join(esc(c) for c in r) + " |" for r in rows) + "\n"


def fig(fid: str, label: str, expected, actual, ok: bool | None, detail: str = "", scored: bool = True, goal: bool = False) -> dict:
    return {"id": fid, "label": label, "expected": expected, "actual": actual, "ok": ok, "detail": detail, "scored": scored and ok is not None, "goal": goal}


def close(a, b, rel=1e-6, abs_=0.011) -> bool:
    try:
        return abs(float(a) - float(b)) <= max(abs_, rel * abs(float(b)))
    except (TypeError, ValueError):
        return False


# ============================================================================ run the pipeline
def run_pipeline(d: dict, goal: str, timing: Timing, say) -> dict:
    from app import llm
    from app.runner import create_run, read_status, resume_with_answers, run_dir, start_run
    folder = REVIEW / d["folder"]
    rid = create_run(goal, [(f, (folder / f).read_bytes()) for f in d["files"]], context=None)
    say(f"  run {rid} started")
    t0 = time.monotonic()
    start_run(rid)
    asked: list[dict] = []
    given: dict = {}

    def wait() -> str:
        while True:
            st = (read_status(rid) or {}).get("status")
            if st in ("done", "error", "needs_input"):
                return st
            if time.monotonic() - t0 > RUN_TIMEOUT_S:
                return "timeout"
            time.sleep(2)

    st = wait()
    if st == "needs_input":
        qs = json.loads((run_dir(rid) / "questions.json").read_text(encoding="utf-8"))["questions"]
        given = {q["id"]: q.get("prefill") or q["default"] for q in qs}
        asked = qs
        say(f"  paused with {len([q for q in qs if not q.get('auto')])} question(s); answering with the defaults")
        resume_with_answers(rid, {"answers": given, "skip": False, "notes": ""})
        st = wait()
    else:
        qf = run_dir(rid) / "questions.json"
        if qf.exists():
            asked = json.loads(qf.read_text(encoding="utf-8")).get("questions", [])
    return {"rid": rid, "wd": run_dir(rid), "status": st, "seconds": time.monotonic() - t0, "questions": asked, "given": given, "error": (read_status(rid) or {}).get("error")}


# ============================================================================ what the run holds
def sig(o: dict) -> tuple:
    a = o.get("action") or {}
    return (a.get("type"), json.dumps({k: v for k, v in a.items() if k != "type"}, sort_keys=True, default=str))


def question_rows(questions: list[dict], given: dict) -> list[dict]:
    out = []
    for q in questions:
        opts = q.get("options") or []
        dflt = next((o for o in opts if o.get("value") == q.get("default")), None)
        chosen = given.get(q.get("id"), q.get("default"))
        real = lambda o: (o.get("action") or {}).get("type") != "note_only"
        changes = bool(dflt) and any(sig(o) != sig(dflt) and (real(o) or real(dflt)) for o in opts)
        out.append({"id": q.get("id"), "kind": q.get("kind"), "text": q.get("text"), "why": q.get("why"), "auto": bool(q.get("auto")), "default": q.get("default"), "answered": chosen,
                    "options": [{"value": o.get("value"), "label": o.get("label"), "does": (o.get("action") or {}).get("type")} for o in opts], "changes_a_number": changes})
    return out


def column_rows(res: dict) -> list[dict]:
    u = (res.get("understanding") or {})
    ucols = u.get("columns") or {}
    out = []
    for c in (res.get("profile") or {}).get("columns", []):
        r = ucols.get(c["name"]) or {}
        out.append({"column": c["name"], "role": c.get("role"), "ai_role": r.get("role"), "name_shown": r.get("display_name"), "meaning": r.get("meaning"), "kind": r.get("measure_kind")})
    return out


def null_ids(res: dict) -> set:
    """Ids of findings the curation marked as null results (no reliable difference)."""
    return {c.get("id") for c in (res.get("curation") or {}).get("candidates") or [] if c.get("null_result")}


def chart_stats(tabs: dict) -> dict:
    """Charts per tab, duplicate charts (same type, measure and dimension; the extra copies are counted) and charts made for null results."""
    per_tab, dup, null = {}, 0, 0
    for key, v in tabs.items():
        charts = v.get("charts") or []
        per_tab[key] = len(charts)
        seen: set = set()
        for c in charts:
            k = (c.get("type"), c.get("measure"), c.get("dimension"))
            if c.get("measure") or c.get("dimension"):          # a chart with no known measure and dimension cannot be called a duplicate
                dup += k in seen
                seen.add(k)
            null += bool(c.get("null_result"))
    return {"per_tab": per_tab, "duplicates": dup, "null_charts": null}


def left_out_shown(res: dict) -> int:
    """The number the Data tab shows as 'minor findings left out' (0 when the run has no curation)."""
    return int((res.get("curation") or {}).get("left_out_count") or 0)


def tab_view(res: dict, wd: Path) -> dict:
    from app import viewedit
    cat = viewedit.catalogue(res)
    findings = {f.get("id"): f for f in res.get("findings") or []}
    view: dict = {}
    for key, label, backs in NAV:
        items = [i for i in cat["items"].values() if i["tab"] in backs]
        v = {"label": label, "headings": [i["title"] for i in items if i["kind"] == "section"], "charts": [], "insights": [], "numbers": []}
        for i in items:
            if i["kind"] == "chart":
                f = next((findings[x] for x in i.get("finding_ids") or [] if x in findings), None)
                v["charts"].append({"tab": label, "title": i["title"], "type": (f or {}).get("chart_type") or "image", "subtitle": sentences((f or {}).get("chart_why") or i.get("text") or "", 1),
                                    "measure": str((f or {}).get("measure") or ""), "dimension": str((f or {}).get("dimension") or ""),
                                    "null_result": any(x in null_ids(res) for x in i.get("finding_ids") or [])})
            elif i["kind"] in ("insight", "strategy"):
                v["insights"].append({"title": i["title"], "text": sentences(i.get("text") or i["title"], 2)})
            elif i["kind"] == "card" and key in ("report", "data"):
                v["numbers"].append(f'{i["title"]}' + (f': {sentences(i.get("text"), 1)}' if i.get("text") and i["text"] != i["title"] else ""))
        if key == "report":
            v["numbers"] = [f'{n["label"]}: {n["value"]}' for n in (res.get("report_view") or {}).get("numbers", [])] or v["numbers"]
        if key == "dashboard":
            from app.dashboard import build_dashboard
            try:
                dash = build_dashboard(wd, res)
                v["numbers"] = [f'{m["label"]} ({m["agg"]})' for m in dash.get("measures", [])] + [f'{dash.get("total_rows")} rows in the dashboard slice']
                for m in dash.get("measures", [])[:1]:
                    v["charts"].insert(0, {"tab": label, "title": f'{m["label"]} per period (trend)', "type": "line (drawn in the browser)", "subtitle": "exact title and subtitle: run with --screenshots"})
                    v["charts"].insert(1, {"tab": label, "title": f'share of {m["label"]} by a category', "type": "share (drawn in the browser)", "subtitle": "exact title and subtitle: run with --screenshots"})
                    for dm in dash.get("dims", [])[:3]:
                        v["charts"].append({"tab": label, "title": f'{m["label"]} by {dm["col"]}', "type": "ranked bars (drawn in the browser)", "subtitle": "exact title and subtitle: run with --screenshots"})
            except Exception as e:      # noqa: BLE001
                v["numbers"] = [f"dashboard spec unavailable: {type(e).__name__}"]
        if key == "data":
            dm = res.get("data_model") or {}
            p = res.get("profile") or {}
            v["numbers"] = [f'rows analysed: {p.get("rows")}', f'columns: {p.get("cols")}', f'tables loaded: {len(dm.get("tables", [])) or 1}'] + v["numbers"][:6]
        view[key] = v
    return view


def unavailable(res: dict) -> list[dict]:
    out = []
    for dcs in res.get("decisions") or []:
        name = str(dcs.get("decision", ""))
        if name.startswith("AI unavailable") or name.startswith("stage failed"):
            out.append({"stage": name.split(":", 1)[-1].strip(), "kind": "failed" if name.startswith("stage failed") else "unavailable", "reason": str(dcs.get("why") or "")[:200]})
    for w in res.get("warnings") or []:
        if str(w).startswith("stage failed") and not any(w.startswith("stage failed: " + o["stage"]) for o in out):
            out.append({"stage": str(w)[14:60], "kind": "failed", "reason": str(w)[:200]})
    return out


def report_text(res: dict) -> str:
    """What the Report tab shows: the report text, the key figures and the Report tab's catalogue items."""
    from app import viewedit
    parts = [re.sub(r"</?(?:details|summary)[^>]*>|<a id=[^>]*></a>", "", res.get("report_md") or "")]
    for m in list(res.get("key_metrics") or []) + list((res.get("report_view") or {}).get("numbers") or []):
        parts.append(f'{m.get("label")}: {m.get("value")} {m.get("note", "")}')
    try:
        parts += [f'{i["title"]}. {i.get("text", "")}' for i in viewedit.catalogue(res)["items"].values() if i["tab"] == "report"]
    except Exception:       # noqa: BLE001
        pass
    return "\n".join(parts)


def visible_text(res: dict) -> str:
    from app import viewedit
    parts = [re.sub(r"</?(?:details|summary)[^>]*>|<a id=[^>]*></a>", "", res.get("report_md") or "")]
    for f in res.get("findings") or []:
        parts.append(f'{f.get("title", "")}. {f.get("text", "")}')
    for m in res.get("key_metrics") or []:
        parts.append(f'{m.get("label")}: {m.get("value")} {m.get("note", "")}')
    for m in (res.get("report_view") or {}).get("numbers") or []:      # the Key figures / key numbers lines
        parts.append(f'{m.get("label")}: {m.get("value")} {m.get("note", "")}')
    try:
        parts += [f'{i["title"]}. {i.get("text", "")}' for i in viewedit.catalogue(res)["items"].values()]
    except Exception:       # noqa: BLE001
        pass
    for st in res.get("cleaning_log") or []:       # the Data tab
        parts.append(f'{st.get("step", "")} {st.get("detail", "")}')
    return "\n".join(parts)


# ============================================================================ figures against pandas
class Tables:
    """The tables a figure can be read from: the analysis table (cleaned, joined) for a single file, for the base table and for a stacked set; otherwise the loaded table of that name."""
    def __init__(self, wd: Path, base_stem: str | None, single: bool) -> None:
        import pandas as pd
        from app.dashboard import source_path
        self.pd, self.wd, self.source_path, self.base, self.single = pd, wd, source_path, base_stem, single
        self.analysis = pd.read_pickle(wd / "clean.pkl") if (wd / "clean.pkl").exists() else None
        self._t: dict = {}

    def table(self, fname: str):
        if fname not in self._t:
            try:
                p = self.source_path(self.wd, "tbl:" + stem_of(fname))
                self._t[fname] = self.pd.read_pickle(p) if p else None
            except Exception:       # noqa: BLE001
                self._t[fname] = None
        return self._t[fname]

    def for_col(self, fname: str, col: str):
        """(dataframe, real column name, which table) for an expected file and column."""
        use_analysis = self.single or fname == "stacked" or stem_of(fname) == self.base
        order = ["analysis", "table"] if use_analysis else ["table", "analysis"]
        for which in order:
            df = self.analysis if which == "analysis" else (self.table(fname) if fname != "stacked" else None)
            if df is not None:
                c = find_col(df, col)
                if c is not None:
                    return df, c, "analysis table" if which == "analysis" else f"table {stem_of(fname)}"
        return None, None, "not found"


def left_out_as_empty(res: dict, col: str) -> bool:
    """True when the cleaning log says this column was left out because it had nothing in it."""
    for st in res.get("cleaning_log") or []:
        if "empty" in str(st.get("step", "")).lower() and "left out" in str(st.get("step", "")).lower():
            if repr(col) in str(st.get("detail", "")) or f"'{col}'" in str(st.get("detail", "")):
                return True
    return False


def check_figures(exp: dict, res: dict, wd: Path, text: str, rtext: str | None = None) -> list[dict]:
    """A figure marked "goal": true in expected.json is what the goal asks for: it must be in the Report tab text or key figures (rtext). Every other figure passes if it is anywhere in text."""
    rtext = text if rtext is None else rtext
    from scorecard import has_num
    import pandas as pd
    ans, files = exp["answers"], exp["files"]
    dm = res.get("data_model") or {}
    base_stem = dm.get("base_table")
    T = Tables(wd, base_stem, len(files) == 1)
    out: list[dict] = []
    if T.analysis is None:
        return [fig("table", "analysis table exists", "a cleaned table", "none", False, "clean.pkl was not written")]
    A = T.analysis
    single = len(files) == 1
    tol_shown = lambda v: max(0.5, abs(v) * 0.005)

    # rows of the analysis table
    rc = ans.get("row_count", {})
    if "stacked" in rc:
        out.append(fig("rows", "rows analysed (all six files stacked)", rc["stacked"], len(A), len(A) == rc["stacked"], "" if len(A) == rc["stacked"] else
                       f"the six files were not combined: {len(dm.get('tables', []))} tables loaded, {len(dm.get('unused_tables', []))} ignored, analysis table has {len(A)} rows"))
    else:
        target = next((f for f in files if stem_of(f) == base_stem), None) if base_stem else next(iter(files))
        if target is None:
            target = next(iter(files))
        out.append(fig("rows", f"rows analysed ({stem_of(target)})", rc.get(target), len(A), len(A) == rc.get(target),
                       "" if len(A) == rc.get(target) else f"expected the cleaned {target}; the app's analysis table has {len(A)}"))

    def shown(v, goal: bool = False) -> bool:
        return has_num(rtext if goal else text, float(v), tol_shown(float(v)))

    # main total, extra sums
    totals = []
    if "main_total" in ans:
        totals.append(("total", ans["main_total"]["file"], ans["main_total"]["column"], ans["main_total"]["value"], ans["main_total"]["means"]))
    for k, v in ans.items():
        if isinstance(v, dict) and v.get("means") and k != "main_total" and "value" in v and v.get("column"):
            totals.append((k, v["file"], v["column"], v["value"], v["means"]))
    for k, fname, col, want, means in totals:
        goal = bool(ans["main_total" if k == "total" else k].get("goal"))
        df, c, where = T.for_col(fname, col)
        if df is None:
            out.append(fig(f"{k}-computed", f"{means}: sum of {col}", want, "column not found", False, "the column is not in the analysis table or the loaded table"))
        else:
            got = float(pd.to_numeric(df[c], errors="coerce").sum())
            out.append(fig(f"{k}-computed", f"{means}: sum of {col} ({where})", want, round(got, 2), close(got, want), "" if close(got, want) else f"difference {got - want:,.2f}"))
        out.append(fig(f"{k}-shown", f"{means}: the figure appears in the {'Report tab (goal figure)' if goal else 'app'}", want, "found" if shown(want, goal) else "not found", shown(want, goal),
                       "goal figure: searched in the Report tab text and key figures, within 0.5%" if goal else "search of the visible text of all tabs, within 0.5%", goal=goal))

    # top 3
    tops = [("top_3_main_category", ans.get("top_3_main_category"))] + [(k, v) for k, v in ans.items() if isinstance(v, dict) and "group_by" in v and k != "top_3_main_category"]
    for k, t in tops:
        if not t or not t.get("items"):
            continue
        df, g, where = T.for_col(t["file"], t["group_by"])
        df2, r, _ = T.for_col(t["file"], t["ranked_by"])
        want = t["items"]
        if df is None or df2 is None or df is not df2:
            out.append(fig(f"{k}-computed", f"top 3 {t['group_by']} by {t['ranked_by']}", want, "columns not in one table", False, "group or value column missing from the table that holds the other"))
        else:
            s = df.assign(_v=pd.to_numeric(df[r], errors="coerce")).groupby(df[g].astype(str))["_v"].sum().sort_values(ascending=False).head(3)
            got = [{"name": a, "total": round(float(b), 2)} for a, b in s.items()]
            ok = len(got) == len(want) and all(x["name"] == y["name"] and close(x["total"], y["total"]) for x, y in zip(got, want))
            out.append(fig(f"{k}-computed", f"top 3 {t['group_by']} by {t['ranked_by']} ({where})", [f"{x['name']} {x['total']:,.0f}" for x in want], [f"{x['name']} {x['total']:,.0f}" for x in got], ok))
        first = want[0]
        goal = bool(t.get("goal"))
        in_text = first["name"].lower() in (rtext if goal else text).lower() and shown(first["total"], goal)
        out.append(fig(f"{k}-shown", f"top {t['group_by']} '{first['name']}' and its total appear in the {'Report tab (goal figure)' if goal else 'app'}", f"{first['name']} {first['total']:,.0f}", "found" if in_text else "not found", in_text,
                       "goal figure: searched in the Report tab text and key figures" if goal else "search of the visible text of all tabs", goal=goal))

    # distinct keys, only on the tables they come from
    dk = ans.get("distinct_key_counts", {})
    bad, n_ok = [], 0
    for key, want in dk.items():
        fname, col = key.rsplit(".", 1)
        if fname not in files and fname != "stacked":
            continue
        df, c, where = T.for_col(fname, col)
        if df is None:
            bad.append(f"{key}: column not found")
            continue
        got = int(df[c].nunique(dropna=True))
        if got == want:
            n_ok += 1
        else:
            bad.append(f"{key}: expected {want}, got {got}")
    if dk:
        out.append(fig("distinct", f"distinct key counts ({len(dk)} keys)", len(dk), n_ok, not bad, "; ".join(bad[:6])))

    # missing values and duplicates: only when one file is the whole analysis
    if single:
        fname = next(iter(files))
        miss = ans.get("missing_values_per_column", {}).get(fname, {})
        diffs = []
        for col, want in miss.items():
            c = find_col(A, col)
            if c is None:
                if not left_out_as_empty(res, col):
                    diffs.append(f"{col}: column not in the cleaned table")
            elif int(A[c].isna().sum()) != want:
                diffs.append(f"{col}: expected {want} blank, got {int(A[c].isna().sum())}")
        if miss:
            out.append(fig("missing", f"blank cells per column ({len(miss)} columns)", len(miss), len(miss) - len(diffs), not diffs, "; ".join(diffs[:6])))
        dup = ans.get("duplicates", {}).get(fname, {}).get("exact_duplicate_rows")
        if dup is not None:
            log = " ".join(f'{s.get("step", "")} {s.get("detail", "")}' for s in res.get("cleaning_log") or [])
            loaded = next((m["rows"] for m in (res.get("data_model") or {}).get("tables", []) if m["name"] == stem_of(fname)), None)
            removed = (loaded - len(A)) if loaded is not None else None
            hit = (str(dup) in log and "duplicate" in log.lower()) if dup else ("exact duplicate" not in log.lower())
            out.append(fig("duplicates", "exact duplicate rows found and removed", dup, f"rows loaded {loaded}, analysed {len(A)}", hit, "checked in the cleaning log" if hit else
                           f"the cleaning log does not say {dup} duplicates were removed (rows dropped in all: {removed})"))
    return out


def check_connections(exp: dict, res: dict, wd: Path) -> tuple[list[dict], dict | None]:
    joins = exp["answers"].get("joins")
    files = exp["files"]
    if not joins or len(files) < 2:
        return [], None
    dm = res.get("data_model") or {}
    rels = dm.get("relationships") or []
    A = __import__("pandas").read_pickle(wd / "clean.pkl")
    base = dm.get("base_table")
    out, linked, rows_ok, rows_n, orph_ok, orph_n = [], 0, 0, 0, 0, 0
    for j in joins:
        ls, rs = stem_of(j["left"]), stem_of(j["right"])
        rel = next((r for r in rels if {r["child_table"], r["parent_table"]} == {ls, rs}), None)
        label = f'{ls} -> {rs} on {j["on"]}'
        found = bool(rel) and rel.get("status") != "rejected"
        out.append(fig("link-" + label, f"link found: {label}", "a link", (f'{rel["child_table"]}.{rel["child_col"]} -> {rel["parent_table"]}.{rel["parent_col"]} (status: {rel.get("status")}; {rel.get("reason", "")})' if rel else "none"),
                       found, "" if rel else "no relationship between these two tables"))
        linked += 1 if out[-1]["ok"] else 0
        if rel and rel["child_table"] == ls:
            unmatched = round((1 - rel["child_row_match"]) * j["left_rows"])
            ok = unmatched == j["left_rows_with_no_match"]
            out.append(fig("orph-" + label, f"orphan rows ({ls} rows with no match in {rs})", j["left_rows_with_no_match"], unmatched, ok, "from the app's match rate, rounded"))
            orph_n += 1
            orph_ok += ok
        elif rel:
            out.append(fig("orph-" + label, f"orphan rows ({ls} rows with no match in {rs})", j["left_rows_with_no_match"], "link goes the other way", None, "direction differs, not scored", scored=False))
        if rel and rel.get("status") == "used" and ls == base:
            ok = len(A) == j["left_join_rows"]
            out.append(fig("rows-" + label, f"joined row count ({ls} left join {rs})", j["left_join_rows"], len(A), ok,
                           "" if ok else (f"the app kept {len(A)} rows; plain pandas gives {j['left_join_rows']} because {rs} repeats a key" if len(A) == j["left_rows"] else "")))
            rows_n += 1
            rows_ok += ok
    used = len(dm.get("tables", [])) - len(dm.get("unused_tables", []))
    summary = {"links": f"{linked}/{len(joins)}", "joined_rows": f"{rows_ok}/{rows_n}", "orphans": f"{orph_ok}/{orph_n}", "tables_used": f'{used} of {len(dm.get("tables", []))}', "base": base,
               "text": f"links found {linked}/{len(joins)}; joined rows right {rows_ok}/{rows_n}; orphan counts right {orph_ok}/{orph_n}; tables connected {used} of {len(dm.get('tables', []))}"}
    return out, summary


# ============================================================================ Lens
def numbers_in(text: str) -> list[tuple[float, bool]]:
    from scorecard import nums
    return [(v, p) for v, p in nums(text) if abs(v) >= 100 or p]


def flat_numbers(o, acc=None) -> list[float]:
    acc = [] if acc is None else acc
    if isinstance(o, bool):
        return acc
    if isinstance(o, (int, float)):
        acc.append(float(o))
    elif isinstance(o, dict):
        for v in o.values():
            flat_numbers(v, acc)
    elif isinstance(o, (list, tuple)):
        for v in o:
            flat_numbers(v, acc)
    return acc


def facts_for(A, exp: dict, g, r) -> list[float]:
    import pandas as pd
    f = flat_numbers(exp["answers"])
    f += [float(len(A))]
    if g is not None and r is not None:
        s = A.assign(_v=pd.to_numeric(A[r], errors="coerce")).groupby(A[g].astype(str))["_v"].sum()
        tot = float(s.sum())
        f += [float(x) for x in s.values] + ([100 * float(x) / tot for x in s.values] if tot else []) + [tot, float(A[r].mean()), float(A[g].nunique())]
        f += [float(x) for x in A.assign(_v=pd.to_numeric(A[r], errors="coerce")).groupby(A[g].astype(str))["_v"].mean().values]
        f += [float(x) for x in A.groupby(A[g].astype(str)).size().values]
    return f


def number_check(text: str, facts: list[float]) -> tuple[str, str]:
    figs = numbers_in(text)
    if not figs:
        return "NO FIGURES", "the answer states no figure to check"
    miss = []
    for v, p in figs:
        if not any(close(v, f, rel=0.006, abs_=0.06) for f in facts):
            miss.append(f"{v:,.2f}{'%' if p else ''}")
    if not miss:
        return "PASS", f"all {len(figs)} figures match a pandas figure"
    return "UNCHECKED", f"{len(miss)} of {len(figs)} figures are not among the pandas figures I computed (not necessarily wrong): " + ", ".join(miss[:6])


def pick_targets(exp: dict, A):
    import pandas as pd
    t = exp["answers"].get("top_3_main_category")
    if t:
        g = find_col(A, t["group_by"])
        r = find_col(A, t["ranked_by"])
        if g is not None and r is not None:
            return g, r
    cats = [c for c in A.columns if A[c].dtype == object and 2 <= A[c].nunique() <= 30 and not str(c).lower().endswith("id")]
    nums_ = [c for c in A.columns if pd.api.types.is_numeric_dtype(A[c]) and A[c].nunique() > 5 and not norm(c).endswith("id")]
    return (cats[0] if cats else None), (nums_[0] if nums_ else None)


def lens_questions(wd: Path, res: dict, exp: dict, timing: Timing, model, budget: Budget, say) -> list[dict]:
    import pandas as pd
    from app import llm, viewedit
    from app.lens import chat, store
    A = pd.read_pickle(wd / "clean.pkl")
    g, r = pick_targets(exp, A)
    out: list[dict] = []
    if g is None or r is None:
        return [{"n": i, "kind": k, "question": "(no category and measure to ask about)", "status": "SKIPPED", "detail": "", "answer": "", "time": dict.fromkeys(("total", "ai", "limit", "own", "calls", "tokens"), 0)}
                for i, k in enumerate(["top-N", "why", "custom chart", "hide/show edit", "brief"], 1)]
    s = A.assign(_v=pd.to_numeric(A[r], errors="coerce")).groupby(A[g].astype(str))["_v"].sum().sort_values(ascending=False)
    top1 = s.index[0]
    facts = facts_for(A, exp, g, r)
    gl, rl = str(g), str(r)
    cat = viewedit.catalogue(res)
    hide_item = next((i for i in cat["items"].values() if i["tab"] == "insights" and i["kind"] in ("chart", "insight")), None) or next((i for i in cat["items"].values() if i["kind"] in ("chart", "card")), None)

    def rows_of(t):
        return t["answer"].get("table", {}).get("rows", [])

    def v_topn(t):
        want = [(a, float(b)) for a, b in list(s.items())[:3]]
        got = [(str(x[0]), float(x[1])) for x in rows_of(t)[:3] if len(x) > 1 and isinstance(x[1], (int, float))]
        return ("PASS" if len(got) == 3 and all(a == c and close(b, d) for (a, b), (c, d) in zip(want, got)) else "FAIL"), f"pandas: {', '.join(f'{a} {b:,.0f}' for a, b in want)}; Lens table: {', '.join(f'{a} {b:,.0f}' for a, b in got) or 'no table'}"

    def v_why(t):
        return number_check(t["answer"].get("text", "") + " " + json.dumps(t["answer"].get("table", {}), default=str), facts)

    def v_chart(t):
        ch = (t["answer"].get("chart") or {}).get("type")
        want = {a: float(b) for a, b in s.items()}
        got = {str(x[0]): float(x[1]) for x in rows_of(t) if len(x) > 1 and isinstance(x[1], (int, float))}
        ok = bool(got) and all(k in want and close(v, want[k]) for k, v in got.items())
        return ("PASS" if ok and ch else "FAIL"), f"chart type {ch}; {len(got)} rows, {sum(1 for k, v in got.items() if k in want and close(v, want[k]))} match pandas"

    def v_hide(t):
        hidden = set(store.get_view_state(wd).get("hidden") or [])
        return ("PASS" if hidden - before_hidden else "FAIL"), f"items hidden now: {sorted(hidden)[:4]}" + (f"; asked to hide '{hide_item['title'][:50]}'" if hide_item else "")

    def v_brief(t):
        a = t["answer"]
        text = a.get("text", "") + " " + json.dumps(a.get("brief") or a.get("doc") or {}, default=str)
        return number_check(text, facts)

    before_hidden: set = set()
    plan = [("top-N", f"What are the top 3 {gl} by total {rl}?", v_topn, "Insights"),
            ("why", f"Why is {top1} the highest {gl} by {rl}?", v_why, "Report"),
            ("custom chart", f"Draw a bar chart of total {rl} by {gl}", v_chart, "Report"),
            ("hide/show edit", f'Hide "{(hide_item or {"title": "the first chart"})["title"][:70]}"', v_hide, "Insights"),
            ("brief", f"Write a brief for {LENS_READER} about {gl}", v_brief, "Report")]
    for n, (kind, q, verify, tab) in enumerate(plan, 1):
        if budget.exceeded(timing):
            out.append({"n": n, "kind": kind, "question": q, "status": "SKIPPED", "detail": "token budget reached", "answer": "", "time": dict.fromkeys(("total", "ai", "limit", "own", "calls", "tokens"), 0)})
            continue
        before_hidden = set(store.get_view_state(wd).get("hidden") or [])
        llm.begin_run()
        acc = timing.open()
        try:
            t = chat.ask(wd, res, question=q, tab=tab, llm=model, allow_ai=True)
            m = timing.close(acc)
            a = t["answer"]
            if kind != "hide/show edit" and a.get("status") != "answered":
                status, detail = "FAIL" if kind in ("top-N", "custom chart") else "NOT ANSWERED", f'answer status "{a.get("status")}"'
            else:
                status, detail = verify(t)
            out.append({"n": n, "kind": kind, "question": q, "status": status, "detail": detail, "answer": sentences(a.get("text", ""), 3), "time": m, "answer_status": a.get("status"), "ai_calls": a.get("ai_calls")})
        except Exception as e:      # noqa: BLE001
            m = timing.close(acc)
            out.append({"n": n, "kind": kind, "question": q, "status": "ERROR", "detail": f"{type(e).__name__}: {e}"[:200], "answer": "", "time": m})
        say(f"    Lens Q{n} ({kind}): {out[-1]['status']}  {out[-1]['time']['total']:.1f}s")
    return out


# ============================================================================ screenshots (one browser at a time)
def screenshots(rid: str, outdir: Path, say) -> dict:
    import shutil
    outdir.mkdir(parents=True, exist_ok=True)
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        return {"error": "frontend/dist is missing: run `cd frontend && npm run build` first"}
    npm = shutil.which("npm")
    port = int(os.environ.get("REVIEW_PORT", "8011"))
    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1"}
    server = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=2)
                break
            except Exception:       # noqa: BLE001
                time.sleep(1)
        else:
            return {"error": "the server did not start"}
        root = subprocess.run(f'"{npm}" root -g' if npm else "npm root -g", shell=True, capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["node", str(Path(__file__).with_name("review_shots.js")), base, rid, str(outdir)], env={**os.environ, "NODE_PATH": root}, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return {"error": (r.stderr or r.stdout)[-400:]}
        dom = outdir / "dom.json"
        return {"files": sorted(p.name for p in outdir.glob("*.png")), "dom": json.loads(dom.read_text(encoding="utf-8")) if dom.exists() else {}}
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except Exception:       # noqa: BLE001
            server.kill()


# ============================================================================ write the review
def stage_rows(stages: dict) -> list[list]:
    return [[n, fmt_s(s["total"]), fmt_s(s["ai"]), fmt_s(s["limit"]), fmt_s(s["own"]), s["calls"], f'{s["tokens"]:,}'] for n, s in stages.items()]


def write_review(out: Path, d: dict, rec: dict, shots: dict | None) -> None:
    L: list[str] = []
    A = L.append
    A(f'# Review of {d["id"]}: {d["folder"]}\n')
    A(f'- Run: {rec["started"]} · commit `{rec["commit"]}` · model `{rec["model"]}` · run folder `{rec["run_id"]}`')
    A(f'- AI mode: **{rec["ai_mode"]}** (real = every AI call answered · partly = some unavailable · rules-only = no AI answer, so not a real-AI result · dry-run = fake model)')
    A(f'- Status: **{rec["status"].upper()}**' + (f' ({rec["status_note"]})' if rec.get("status_note") else ""))
    A(f'- Goal given: "{rec["goal"]}"')
    A(f'- Files: {", ".join(d["files"])}')
    A(f'- Figures: **{rec["passed"]}/{rec["total"]} correct ({rec["pct"]:.0f}%)** · questions asked {rec["questions"]["asked"]} (needed {rec["questions"]["needed"]}, not needed {rec["questions"]["not_needed"]}) · '
      f'AI partly unavailable in {rec["unavailable_count"]} stage(s) · tokens {rec["tokens"]:,} · time {fmt_s(rec["total_s"])} s ({fmt_s(rec["total_wo_limit_s"])} s without limit waiting)')
    if rec.get("connection"):
        A(f'- Connections: {rec["connection"]["text"]}')
    A(f'- What this dataset tests: {rec["what_it_tests"]}\n- What the app should do: {rec["app_should"]}\n')

    A("## 1. Clarifying questions\n")
    qs = rec["question_rows"]
    if not qs:
        A("No question was asked.\n")
    for q in qs:
        A(f'**{q["text"]}**' + ("  _(answered automatically, not asked)_" if q["auto"] else "") + f'\n- why: {q["why"]}\n- would the answer change a number: **{"yes" if q["changes_a_number"] else "no"}**' +
          f'\n- default: `{q["default"]}`; answered with: `{q["answered"]}`')
        A(md_table(["option", "label", "does"], [[o["value"], o["label"], o["does"]] for o in q["options"]]))

    A("## 2. How the AI described each column\n")
    A(md_table(["column", "role", "AI role", "shown as", "AI meaning", "kind"], [[c["column"], c["role"], c["ai_role"] or "", c["name_shown"] or "", c["meaning"] or "", c["kind"] or ""] for c in rec["columns"]]))

    A("## 3. The five tabs\n")
    for key, label, _ in NAV:
        v = rec["tabs"][key]
        A(f"### {label}\n")
        A("- headings: " + (" · ".join(v["headings"]) or "none"))
        A("- key numbers: " + (" · ".join(v["numbers"][:14]) or "none"))
        if v["insights"]:
            A("- items (first two sentences):")
            for i in v["insights"][:20]:
                A(f'  - **{sentences(i["title"], 1)[:110]}** {i["text"]}')
        A("")

    cs = rec.get("chart_stats") or {}
    A("## 4. Every chart shown\n")
    A("Charts per tab: " + " · ".join(f'{lbl} {cs.get("per_tab", {}).get(k, 0)}' for k, lbl, _ in NAV) + f' · duplicate charts (same type, measure and dimension): {cs.get("duplicates", 0)}'
      f' · charts made for null results: {cs.get("null_charts", 0)} · left out (the number the Data tab shows): {rec.get("left_out_shown", 0)}\n')
    A(md_table(["tab", "title", "chart type", "subtitle"], [[c["tab"], c["title"][:90], c["type"], c["subtitle"][:140]] for k, _, _ in NAV for c in rec["tabs"][k]["charts"]]))
    if shots and shots.get("dom"):
        A("### As seen in the browser (1440 px)\n")
        for tab, v in shots["dom"].items():
            A(f'- **{tab}**: headings {" · ".join(v.get("headings", [])[:12])}')
            for c in v.get("cards", [])[:30]:
                A(f'  - card: {c.get("title")} — {c.get("sub")}')

    A("## 5. Figures against pandas\n")
    A(md_table(["", "figure", "pandas expected", "the app", "note"], [[(("PASS" if f["ok"] else "FAIL") + (" (goal)" if f.get("goal") else "")) if f["scored"] else "INFO", f["label"], json.dumps(f["expected"], default=str)[:90], json.dumps(f["actual"], default=str)[:90], f["detail"]]
                                                                   for f in rec["figures"]]))
    if rec.get("info_figures"):
        A("Other expected figures (not scored; found by searching the visible text for the number, so a hit can be a coincidence):\n")
        A(md_table(["figure", "expected", "number found in the text"], rec["info_figures"]))
    if rec.get("connection"):
        A("## 6. How the files were connected\n")
        c = rec["connection"]
        A(f'- analysis grain (base table): `{c["base"]}` · {c["text"]}')
        A(md_table(["", "check", "pandas expected", "the app", "note"], [[("PASS" if f["ok"] else "FAIL") if f["scored"] else "INFO", f["label"], json.dumps(f["expected"], default=str)[:80], json.dumps(f["actual"], default=str)[:100], f["detail"]]
                                                                         for f in rec["connection_figures"]]))
        A("The app's own notes on joining:\n")
        for w in rec.get("join_notes", [])[:12]:
            A(f"- {w}")
        A("")

    A("## 7. Where the AI was partly unavailable\n")
    A(md_table(["stage", "what happened", "reason"], [[u["stage"], u["kind"], u["reason"]] for u in rec["unavailable"]]) if rec["unavailable"] else "None.\n")
    if rec.get("status_note"):
        A(f'Run note: {rec["status_note"]}\n')

    A("## 8. Tokens\n")
    A(f'{rec["tokens"]:,} tokens in {rec["calls"]} AI call(s) for the run (input {rec["tokens_in"]:,}, output {rec["tokens_out"]:,}); the Lens questions used {rec["lens_tokens"]:,} more.\n')

    A("## 9. Time by stage\n")
    A(md_table(["stage", "total s", "AI wait s", "limit wait s", "own compute s", "calls", "tokens"], stage_rows(rec["stages"]) + [["(outside the stages: start-up, saving, resuming)", fmt_s(rec["outside_s"]), "", "", "", "", ""]]))
    A(f'**Total run time {fmt_s(rec["total_s"])} s; without limit waiting {fmt_s(rec["total_wo_limit_s"])} s.** (limit waiting = pacing to stay inside the tokens-a-minute window, plus any rate-limit retry wait; '
      f'AI wait = time inside the model call; own compute = the rest.) Stages that ran more than once (the critic loop, a resume after questions) are added together.\n')
    A("**Slowest three:** " + ", ".join(f'{n} {fmt_s(s)} s' for n, s in rec["slowest3"]) + "\n")

    A("## 10. Five Lens questions\n")
    for q in rec["lens"]:
        A(f'**Q{q["n"]} ({q["kind"]}): {q["question"]}** → **{q["status"]}**\n- answer: {q["answer"] or "(none)"}\n- check: {q["detail"]}')
    A("")
    A(md_table(["question", "total s", "AI wait s", "limit wait s", "own compute s", "calls", "tokens"],
               [[f'Q{q["n"]} {q["kind"]}', fmt_s(q["time"]["total"]), fmt_s(q["time"]["ai"]), fmt_s(q["time"]["limit"]), fmt_s(q["time"]["own"]), q["time"]["calls"], f'{q["time"]["tokens"]:,}'] for q in rec["lens"]]))
    lt = rec["lens_time"]
    A(f'**Lens total {fmt_s(lt["total"])} s; without limit waiting {fmt_s(lt["total"] - lt["limit"])} s.**\n')
    if shots:
        A("## 11. Screenshots\n")
        A(shots["error"] if shots.get("error") else "Saved in `review_out/" + d["folder"] + "/`: " + ", ".join(shots.get("files", [])))
    (out / f'{d["folder"]}.md').write_text("\n".join(L) + "\n", encoding="utf-8")


# ============================================================================ records: summary and scoreboard
SCORE_HEAD = ["date_time", "dataset", "commit", "figures_passed", "figures_total", "percent_correct", "questions_asked", "questions_needed", "questions_not_needed", "connection_result",
              "tokens", "total_seconds", "seconds_without_limit_waiting", "ai_partly_unavailable_stages", "lens_pass", "lens_scored", "status", "ai_mode"]
SCORE_EXTRA = ["charts_report", "charts_insights", "charts_ideas", "charts_dashboard", "charts_data", "duplicate_charts", "null_result_charts", "left_out_count"]
SCORE_FULL = SCORE_HEAD + SCORE_EXTRA


def score_extra(rec: dict) -> list:
    cs = rec.get("chart_stats") or {}
    pt = cs.get("per_tab", {})
    return [pt.get(k, 0) for k, _, _ in NAV] + [cs.get("duplicates", 0), cs.get("null_charts", 0), rec.get("left_out_shown", 0)]


def extend_header(path: Path, change, is_old) -> None:
    """Add the new column names to an old header line only. Every earlier data row is left exactly as it was (it just has fewer cells)."""
    lines = path.read_bytes().decode("utf-8").split("\n")
    for n, ln in enumerate(lines):
        bare = ln.rstrip("\r")
        if is_old(bare):
            lines[n] = change(bare) + ln[len(bare):]
            path.write_bytes("\n".join(lines).encode("utf-8"))
            return


def append_scoreboard(out: Path, rec: dict) -> None:
    row = [rec["started"], rec["id"], rec["commit"], rec["passed"], rec["total"], f'{rec["pct"]:.1f}', rec["questions"]["asked"], rec["questions"]["needed"], rec["questions"]["not_needed"],
           (rec["connection"] or {}).get("text", "n/a (single file)"), rec["tokens"] + rec["lens_tokens"], f'{rec["total_s"]:.1f}', f'{rec["total_wo_limit_s"]:.1f}', rec["unavailable_count"],
           rec["lens_pass"], rec["lens_scored"], rec["status"], rec["ai_mode"]] + score_extra(rec)
    csv_p, md_p = out / "scoreboard.csv", out / "scoreboard.md"
    new = not csv_p.exists()
    if not new:
        extend_header(csv_p, lambda ln: ln + "," + ",".join(SCORE_EXTRA), lambda ln: ln.startswith("date_time,") and "left_out_count" not in ln)
    with csv_p.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(SCORE_FULL)
        w.writerow(row)
    if not md_p.exists():
        md_p.write_text("# Scoreboard (one row per run; never overwritten)\n\n| " + " | ".join(SCORE_FULL) + " |\n|" + "|".join("---" for _ in SCORE_FULL) + "|\n", encoding="utf-8")
    else:
        extend_header(md_p, lambda ln: ln.rstrip(" |") + " | " + " | ".join(SCORE_EXTRA) + " |", lambda ln: ln.startswith("| date_time |") and "left_out_count" not in ln)
        extend_header(md_p, lambda ln: ln + "|".join("---" for _ in SCORE_EXTRA) + "|", lambda ln: ln.startswith("|---|") and ln.count("---") == len(SCORE_HEAD))
    with md_p.open("a", encoding="utf-8") as f:
        f.write("| " + " | ".join(str(c).replace("|", "/") for c in row) + " |\n")


def latest_records(out: Path, index: list[dict]) -> list[dict]:
    recs = []
    for d in index:
        p = record_path(out, d)
        if p.exists():
            try:
                recs.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return recs


def overall(recs: list[dict], modes: tuple[str, ...] = ("real",)) -> str:
    done = [r for r in recs if r["status"] == "complete" and r.get("ai_mode") in modes]       # not counted: partial runs (token budget), failed runs, and runs where the AI was only partly or not at all available
    skipped = len(recs) - len(done)
    p, t = sum(r["passed"] for r in done), sum(r["total"] for r in done)
    what = "real-AI" if "real" in modes else "dry-run (fake AI, not a real result)"
    if not t:
        return f"Overall ({what}): no figures scored yet ({skipped} latest run(s) not counted)."
    return (f"Overall ({what}): {p}/{t} figures correct = {100 * p / t:.1f}% across {len(done)} complete dataset(s) "
            f"(latest run of each; {skipped} other latest run(s) not counted: partial, failed, partly-AI, rules-only or dry-run).")


def write_summary(out: Path, index: list[dict]) -> None:
    recs = latest_records(out, index)
    L = ["# Review summary (latest run of each dataset)\n", overall(recs, counted_modes(out)) + "\n"]
    L.append(md_table(["dataset", "AI mode", "status", "pass/fail", "questions asked", "three slowest stages", "tokens", "time s (without limit wait)"],
                      [[r["id"], r.get("ai_mode", "?"), r["status"], f'{r["passed"]}/{r["total"]} ({r["pct"]:.0f}%)', f'{r["questions"]["asked"]} (needed {r["questions"]["needed"]})', ", ".join(f'{n} {s:.0f}s' for n, s in r["slowest3"]),
                        f'{r["tokens"] + r["lens_tokens"]:,}', f'{r["total_s"]:.0f} ({r["total_wo_limit_s"]:.0f})'] for r in recs]))
    L.append("\n## Problems, one line each\n")
    for r in recs:
        L.append(f'### {r["id"]} {r["folder"]}')
        for p in r["problems"] or ["no problem found by the checks"]:
            L.append(f"- {p}")
        L.append("")
    todo = [d["id"] for d in index if not record_path(out, d).exists()]
    if todo:
        L.append("Not run yet: " + ", ".join(todo) + "\n")
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8")


# ============================================================================ one dataset, start to finish
def review_one(d: dict, args, out: Path, timing: Timing, budget: Budget, model, label: str, say) -> dict:
    from app import llm
    exp = json.loads((REVIEW / d["folder"] / "expected.json").read_text(encoding="utf-8"))
    top = exp["answers"].get("top_3_main_category")
    goal = args.goal or (f'Which {top["group_by"]} has the highest {top["ranked_by"]}?' if top else GOAL_DEFAULT)
    started = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timing.stages.clear()
    timing.tokens_total = 0
    budget.hit_at, budget.auth_failed = "", False
    llm.begin_run()
    p = run_pipeline(d, goal, timing, say)
    wd = p["wd"]
    status, note = "complete", ""
    if budget.auth_failed:
        status, note = "ai-refused", AUTH_MESSAGE
    elif p["status"] != "done" or not (wd / "result.json").exists():
        status, note = "error", f'the run ended with status "{p["status"]}"' + (f': {p["error"]}' if p["error"] else "")
    elif budget.hit_at:
        status, note = "partial", f"the token budget (--max-tokens {budget.max}) was reached before the {budget.hit_at} stage; from there the stages used their rules only"
    rec: dict = {"id": d["id"], "folder": d["folder"], "started": started, "commit": git_commit(), "model": label, "run_id": p["rid"], "status": status, "status_note": note, "goal": goal,
                 "what_it_tests": exp.get("what_it_tests", ""), "app_should": exp.get("app_should", "")}
    qrows = question_rows(p["questions"], p["given"])
    shown_q = [q for q in qrows if not q["auto"]]
    rec["question_rows"] = shown_q
    rec["questions"] = {"asked": len(shown_q), "needed": sum(q["changes_a_number"] for q in shown_q), "not_needed": sum(not q["changes_a_number"] for q in shown_q)}
    stage_total = sum(s["total"] for s in timing.stages.values())
    rec["stages"] = {n: {k: round(v, 3) if isinstance(v, float) else v for k, v in s.items()} for n, s in timing.stages.items()}
    limit_all = sum(s["limit"] for s in timing.stages.values())
    rec["total_s"] = p["seconds"]
    rec["total_wo_limit_s"] = max(0.0, p["seconds"] - limit_all)
    rec["outside_s"] = max(0.0, p["seconds"] - stage_total)
    rec["slowest3"] = sorted(([n, round(s["total"], 1)] for n, s in timing.stages.items()), key=lambda x: -x[1])[:3]
    rec["tokens"] = timing.tokens_total
    usage = json.loads((wd / "ai_usage.json").read_text(encoding="utf-8")) if (wd / "ai_usage.json").exists() else {"totals": {}}
    tt = usage.get("totals") or {}
    rec["tokens_in"], rec["tokens_out"], rec["calls"] = tt.get("input_tokens", 0), tt.get("output_tokens", 0), tt.get("sent", 0)
    rec["tokens"] = max(rec["tokens"], rec["tokens_in"] + rec["tokens_out"])
    for k in ("columns", "tabs", "figures", "info_figures", "connection_figures", "unavailable", "lens", "join_notes", "problems"):
        rec[k] = [] if k != "tabs" else {key: {"label": label, "headings": [], "charts": [], "insights": [], "numbers": []} for key, label, _ in NAV}
    rec.update(chart_stats={"per_tab": {}, "duplicates": 0, "null_charts": 0}, left_out_shown=0, connection=None, passed=0, total=0, pct=0.0, unavailable_count=0, lens_pass=0, lens_scored=0, lens_tokens=0, lens_time={"total": 0.0, "limit": 0.0})
    shots = None
    if status not in ("error", "ai-refused"):
        res = json.loads((wd / "result.json").read_text(encoding="utf-8"))
        rec["columns"] = column_rows(res)
        rec["tabs"] = tab_view(res, wd)
        rec["unavailable"] = unavailable(res)
        rec["unavailable_count"] = len(rec["unavailable"])
        text = visible_text(res)
        rec["chart_stats"], rec["left_out_shown"] = chart_stats(rec["tabs"]), left_out_shown(res)
        figs = check_figures(exp, res, wd, text, report_text(res))
        cfigs, conn = check_connections(exp, res, wd)
        rec["figures"], rec["connection_figures"], rec["connection"] = figs, cfigs, conn
        scored = [f for f in figs + cfigs if f["scored"]]
        rec["passed"], rec["total"] = sum(1 for f in scored if f["ok"]), len(scored)
        rec["pct"] = 100 * rec["passed"] / rec["total"] if rec["total"] else 0.0
        from scorecard import has_num
        for k, v in exp["answers"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rec["info_figures"].append([k, v, "yes" if has_num(text, float(v), max(0.5, abs(v) * 0.005)) else "no"])
        rec["join_notes"] = [s["detail"] for s in (res.get("data_model") or {}).get("steps", [])] + [w["text"] for w in (res.get("data_model") or {}).get("warnings", [])]
        if model is not None:
            rec["lens"] = lens_questions(wd, res, exp, timing, model, budget, say)
        lt = [q["time"] for q in rec["lens"]]
        rec["lens_time"] = {"total": sum(t["total"] for t in lt), "limit": sum(t["limit"] for t in lt)}
        rec["lens_tokens"] = sum(t["tokens"] for t in lt)
        sc = [q for q in rec["lens"] if q["status"] in ("PASS", "FAIL", "ERROR", "NOT ANSWERED")]
        rec["lens_scored"], rec["lens_pass"] = len(sc), sum(q["status"] == "PASS" for q in sc)
        if args.screenshots:
            say("  taking screenshots (one browser)")
            shots = screenshots(p["rid"], out / d["folder"], say)
        for f in figs + cfigs:
            if f["scored"] and not f["ok"]:
                rec["problems"].append(("GOAL FIGURE MISSING: " if f.get("goal") else "") + f'{f["label"]}: expected {json.dumps(f["expected"], default=str)[:60]}, the app gave {json.dumps(f["actual"], default=str)[:60]}' + (f' ({f["detail"][:100]})' if f["detail"] else ""))
        for u in rec["unavailable"]:
            rec["problems"].append(f'AI {u["kind"]} in {u["stage"]}: {u["reason"][:100]}')
        rec["problems"] += [f'Lens Q{q["n"]} ({q["kind"]}) {q["status"]}: {q["detail"][:100]}' for q in rec["lens"] if q["status"] in ("FAIL", "ERROR", "NOT ANSWERED")]
    rec["ai_mode"] = ai_mode(args.fake_ai, rec["stages"], rec["unavailable_count"])
    if note:
        rec["problems"].insert(0, note)
    (out / d["folder"]).mkdir(parents=True, exist_ok=True)
    write_review(out, d, rec, shots)
    record_path(out, d).write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    budget.spent_before += timing.tokens_total
    timing.tokens_total = 0            # counted once, in spent_before
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description="Real-AI review of the review pack, one dataset at a time.")
    ap.add_argument("--list", action="store_true", help="list the datasets, whether each is done, and the command")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="dataset ids, folder names or prefixes (R01, M03, M)")
    ap.add_argument("--max-tokens", type=int, default=None, metavar="N", help="stop cleanly when this many tokens have been used in this invocation")
    ap.add_argument("--force", action="store_true", help="rerun a dataset that is already done")
    ap.add_argument("--screenshots", action="store_true", help="one screenshot per tab at 1440 px wide, one browser at a time")
    ap.add_argument("--fake-ai", action="store_true", help="dry run with a fake model: no network, no key; writes to review_out/_dryrun")
    ap.add_argument("--out", default=None, help="output folder (default review_out, or review_out/_dryrun with --fake-ai)")
    ap.add_argument("--goal", default=None, help="the goal text to give every dataset (default: built from the dataset's own main category and measure)")
    args = ap.parse_args()

    index = load_index()
    out = Path(args.out) if args.out else ROOT / "review_out" / ("_dryrun" if args.fake_ai else "")
    out = out.resolve()
    if args.list:
        print(f'{"id":<5}{"folder":<42}{"files":>5}{"rows":>7}  {"status":<10} command')
        for d in index:
            print(f'{d["id"]:<5}{d["folder"]:<42}{len(d["files"]):>5}{d["rows"]:>7}  {done_status(out, d):<10} python scripts/review_run.py --only {d["id"]}')
        return
    todo = pick(index, args.only)
    out.mkdir(parents=True, exist_ok=True)
    os.environ["AI_CACHE"] = "off"                      # every call really goes to the model, so the times and tokens are real
    if args.fake_ai:
        import tempfile
        tmp = tempfile.mkdtemp(prefix="review_dry_")
        os.environ.update(LLM_PROVIDER="none", RUNS_DIR=tmp + "/runs", AI_CACHE_DIR=tmp + "/c", GLOSSARY_PATH=tmp + "/g.json", CHART_DPI="50", DATA_DIR=tmp)
    else:
        os.environ.setdefault("AI_TPM_LIMIT", "6000")  # stays under the 8,000 tokens-a-minute free tier (the app's pacing counts the reply reserve too)
    from app import config, graph, llm  # noqa: F401  (loads .env)
    if args.fake_ai:
        from ai_tokens import EmptyJsonAI
        fake = EmptyJsonAI()
        llm.get_llm = lambda: (fake, "fake model (dry run)")
        graph.get_llm = lambda: (fake, "fake model (dry run)")
    model, label = llm.get_llm()
    if model is None:
        sys.exit(f"No AI model is configured ({label}). Put the key in .env, or use --fake-ai for a dry run.")
    timing, budget = Timing(), Budget(args.max_tokens)
    install(timing, budget)
    say = lambda s: print(s, flush=True)
    say(f"Model: {label}   output: {out}   AI_TPM_LIMIT={os.environ.get('AI_TPM_LIMIT', 'unset')}   max tokens: {args.max_tokens or 'none'}")
    ran, skipped = [], []
    for d in todo:
        if budget.exceeded(timing):
            say(f"Token budget reached ({budget.used(timing):,}). Not started: " + ", ".join(x["id"] for x in todo[todo.index(d):]))
            break
        if done_status(out, d) == "complete" and not args.force:
            skipped.append(d["id"])
            say(f'{d["id"]} {d["folder"]}: already done, skipped (use --force to rerun)')
            continue
        say(f'{d["id"]} {d["folder"]}: running')
        rec = review_one(d, args, out, timing, budget, model, label, say)
        ran.append(rec)
        append_scoreboard(out, rec)
        if rec["status"] == "ai-refused":
            say(f"  {AUTH_MESSAGE}. Stopped; no later dataset was started. Check GROQ_API_KEY in .env, then run again.")
            write_summary(out, index)
            sys.exit(2)
        say(f'  {rec["status"].upper()}: {rec["passed"]}/{rec["total"]} figures correct, {rec["tokens"] + rec["lens_tokens"]:,} tokens, {rec["total_s"]:.0f} s -> {out / (d["folder"] + ".md")}')
    write_summary(out, index)
    if ran:
        line = overall(latest_records(out, index), counted_modes(out))
        with (out / "scoreboard.md").open("a", encoding="utf-8") as f:
            f.write(f"\n_{dt.datetime.now():%Y-%m-%d %H:%M:%S} {line}_\n\n")
        say(line)
    say(f"Done: ran {len(ran)}, skipped {len(skipped)}. Tokens used this time: {budget.used(timing):,}. Summary: {out / 'summary.md'}")


if __name__ == "__main__":
    main()
