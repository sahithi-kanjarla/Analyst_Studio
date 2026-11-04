import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from pydantic import BaseModel, Field
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ALLOWED_EXT, FRONTEND_DIST, MAX_FILES, MAX_ROWS, MAX_UPLOAD_MB, RUNS_DIR, VERSION, public_demo, retention_days
from . import clarify, glossary, guard, owner, tiering, viewedit, web
from .profiler import count_delimited_rows, too_many_rows_message
from . import lens
from .lens import chat as lenschat, howtext as lenshow, reports as lensreports, store as lensstore
from .context import normalise_context
from . import dashplan
from .dashboard import build_dashboard, list_sources, source_path, valid_source
from .events import get_events
from .llm import get_llm
from .context import typed_goal
from .runner import DEFAULT_EXPLORE_GOAL, RUN_ID_RE, create_run, ensure_demo_run, is_demo, purge_expired, read_status, rerun_with_answers, rerun_without_derived, resume_with_answers, run_dir, start_run

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    purge_expired()  # old runs are removed when the server starts, then again whenever a new run is created
    yield


app = FastAPI(title="Analyst Studio", version=VERSION, lifespan=_lifespan, default_response_class=web.SafeJSON)
web.install(app)          # cross-origin rules, security headers and the forwarded scheme: see web.py


def _check_id(run_id: str) -> None:
    if not re.match(RUN_ID_RE, run_id) or not run_dir(run_id).exists():
        raise HTTPException(404, "Run not found")


def _writable(run_id: str) -> None:
    """The sample run is read-only: it can be viewed, but never answered, re-run or changed."""
    if is_demo(run_id):
        raise HTTPException(403, "The sample run is read-only. Upload your own data to run an analysis.")


@app.exception_handler(lensstore.ReadOnlyRun)
async def _read_only_run(request: Request, exc: lensstore.ReadOnlyRun):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.post("/api/demo")
def open_demo():
    """The sample run (shipped with the repo, no AI calls, no upload). Returns its run id; the run page shows it like any other run."""
    return {"run_id": ensure_demo_run(), "read_only": True}


@app.get("/api/health")
def health():
    """For a load balancer or a health check: that the server is up, and which version. Nothing else (no model name, no path, no setting)."""
    return {"status": "ok", "version": VERSION}


@app.get("/api/config")
def site_config():
    """What the page needs to know about this site: whether an access code is asked for, which model is behind it, whether the chat is on, and for a public demo how long runs are kept. No secrets and no paths."""
    _, label = get_llm()
    return {"llm": label, "chatbot": lens.mode(), "requires_code": guard.code_required(), "public_demo": public_demo(), "retention_days": retention_days(),
            "daily_ai_runs": guard.cap_of("ai_runs"), "daily_lens_questions": guard.cap_of("lens_questions")}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    """"Delete my run": the run, its uploaded files, its results and its saved conversation are removed at once. The sample report is never deleted."""
    _check_id(run_id)
    _writable(run_id)
    import shutil
    wd = run_dir(run_id)
    n = sum(1 for p in wd.rglob("*") if p.is_file())
    shutil.rmtree(wd, ignore_errors=True)
    if wd.exists():
        raise HTTPException(500, "The run could not be fully deleted. Please try again.")
    return {"deleted": run_id, "files_removed": n}


@app.post("/api/runs")
async def new_run(request: Request, response: Response, goal: str = Form(""), company: str = Form(""), industry: str = Form(""), description: str = Form(""), kpis: str = Form(""),
                  audience: str = Form(""), notes: str = Form(""), currency: str = Form(""), explore: str = Form(""), review_saved: str = Form(""),
                  access_code: str = Form(""), files: list[UploadFile] = File(default=[]), file: UploadFile | None = File(default=None)):
    """Upload one or several data files (or an Excel workbook with several sheets). Several tables are connected automatically.
    Everything except the files is optional: with no goal the agents run in exploratory mode; business context sharpens the analysis."""
    guard.check_code(guard.code_from(request, access_code))     # the sample report never comes through here, so it stays open
    guard.check_upload_rate(request)
    goal = goal.strip()
    if goal and len(goal) < 5:
        raise HTTPException(400, "Please describe your goal in a few words, or leave it empty for an exploratory analysis.")
    is_explore = (not goal) or explore.strip().lower() in ("1", "true", "on", "yes")
    context = normalise_context({"company": company, "industry": industry, "description": description, "kpis": kpis, "audience": audience,
                                 "notes": notes, "currency": currency, "explore": is_explore})
    if not goal:
        goal = DEFAULT_EXPLORE_GOAL
    uploads = list(files) + ([file] if file else [])
    if not uploads:
        raise HTTPException(400, "Attach at least one data file.")
    if len(uploads) > MAX_FILES:
        raise HTTPException(400, f"Please upload at most {MAX_FILES} files at once.")
    items, total = [], 0
    for up in uploads:
        ext = "." + up.filename.rsplit(".", 1)[-1].lower() if up.filename and "." in up.filename else ""
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"Unsupported file type '{up.filename}'. Use one of: {', '.join(sorted(ALLOWED_EXT))}")
        content = await up.read()
        total += len(content)
        if total > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"The files together are larger than {MAX_UPLOAD_MB} MB.")
        if ext in (".csv", ".tsv", ".txt"):
            rows = count_delimited_rows(content)
            if rows > MAX_ROWS:
                raise HTTPException(413, too_many_rows_message(up.filename, rows, MAX_ROWS))
        items.append((up.filename, content))
    if get_llm()[0] is not None:
        guard.reserve_ai_run()           # a run that uses the AI counts against today's cap for all visitors
    purge_expired()
    run_id = create_run(goal[:2000], items, context=context, review_saved=review_saved.strip().lower() in ("1", "true", "on", "yes"),
                        owner=owner.current_owner(request, response))      # the first upload from a browser sets its cookie
    start_run(run_id)
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs(request: Request):
    """The caller's own runs (and the sample run, if it has been opened). Nobody else's."""
    items, caller = [], owner.current_owner(request)
    for d in RUNS_DIR.iterdir():
        st = read_status(d.name) if re.match(RUN_ID_RE, d.name) else None
        if st and owner.can_access(d.name, caller):
            items.append({"run_id": d.name, "goal": typed_goal(st.get("goal")), "status": st.get("status"), "created": st.get("created"), "filename": st.get("filename")})
    return sorted(items, key=lambda x: x.get("created") or 0, reverse=True)[:20]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    _check_id(run_id)
    st = read_status(run_id) or {}
    out = {"run_id": run_id, "status": st.get("status"), "goal": typed_goal(st.get("goal")), "error": st.get("error")}
    if st.get("status") == "queued" and st.get("message"):
        out["message"] = st["message"]          # "Waiting for other runs to finish"
    res = run_dir(run_id) / "result.json"
    if st.get("status") == "done" and res.exists():
        data = json.loads(res.read_text())
        base = f"/api/runs/{run_id}/files/"
        data["report_md"] = (data.get("report_md") or "").replace("](charts/", f"]({base}charts/")
        for f in data.get("findings") or []:
            if f.get("chart"):
                f["chart_url"] = base + f["chart"]
        view = data.get("report_view") or tiering.build_view(data)       # runs made before the tiers existed get them computed from the saved findings
        data["report_view"] = view
        pg = view.get("page")
        if pg:      # the designed report page: its charts need addresses, like the markdown's
            for ch in pg["charts"] + [x for c in pg["cards"] for x in c["charts"]]:
                ch["url"] = base + ch["path"]
            for a in pg["actions"]:
                a["chart_url"] = base + a["chart"] if a.get("chart") else ""
            pg["behind_md"] = pg["behind_md"].replace("](charts/", f"]({base}charts/")
        for c in view.get("evidence", []) + view.get("appendix", []):
            if c.get("chart"):
                c["chart_url"] = base + c["chart"]
        # apply curation (computed after the report; restructures the view)
        curation = data.get("curation")
        if curation:
            tiering.apply_curation(view, curation)
            for c in view.get("report_findings", []) + view.get("more_findings", []):
                if c.get("chart") and not c.get("chart_url"):
                    c["chart_url"] = base + c["chart"]
        out.update(data)
        out["run_id"] = run_id      # the id in the URL, not the one the run was first saved under (a copied run keeps its original id inside result.json)
    return out


def _jsonable(v):
    import pandas as pd
    if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat(sep=" ").replace(" 00:00:00", "")
    if hasattr(v, "item"):
        v = v.item()
    return v if isinstance(v, (int, float, bool, str)) else str(v)


def _load_questions(run_id: str) -> dict:
    p = run_dir(run_id) / "questions.json"
    if not p.exists():
        raise HTTPException(404, "This run has no questions.")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/runs/{run_id}/questions")
def get_questions(run_id: str):
    """The clarifying questions for a run, with defaults and any saved glossary answers pre-filled. Only the questions that need an answer are listed."""
    _check_id(run_id)
    data = _load_questions(run_id)
    ws = glossary.get_workspace(data.get("workspace", "default"))
    qs = [q for q in data["questions"] if not q.get("auto")]
    return {"status": (read_status(run_id) or {}).get("status"), "workspace": data.get("workspace"), "questions": qs,
            "notes": ws["notes"], "definitions": ws["definitions"], "can_skip": True}


class AnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    definitions: dict[str, str] = Field(default_factory=dict)
    skip: bool = False


@app.post("/api/runs/{run_id}/answers")
def post_answers(run_id: str, body: AnswersIn, request: Request):
    """Answer the questions (or skip to use the defaults) and resume the run. Answers are also saved to the company's glossary."""
    _check_id(run_id)
    _writable(run_id)
    guard.check_code(guard.code_from(request))
    data = _load_questions(run_id)
    qmap = {q["id"]: q for q in data["questions"] if not q.get("auto")}
    for qid, val in body.answers.items():
        if qid not in qmap:
            raise HTTPException(400, f"Unknown question '{qid[:60]}'.")
        if val not in clarify.option_values(qmap[qid]):
            raise HTTPException(400, f"'{val[:60]}' is not an option for '{qid[:60]}'.")
    err = glossary.validate_entry({"answers": body.answers, "definitions": body.definitions, "notes": body.notes})
    if err:
        raise HTTPException(422, err)
    if (read_status(run_id) or {}).get("status") != "needs_input":
        raise HTTPException(409, "This run is not waiting for answers.")
    # "Continue" confirms the whole form: questions left untouched keep the pre-filled value the user saw, and that is what gets saved
    confirmed = {qid: body.answers.get(qid) or q.get("prefill") or q["default"] for qid, q in qmap.items()}
    if not body.skip:
        try:
            glossary.merge_answers(data.get("workspace", "default"), confirmed, {i: q["text"] for i, q in qmap.items()}, body.notes, body.definitions,
                                   {i: clarify.option_for(q, confirmed[i])["label"] for i, q in qmap.items()}, {i: clarify.fingerprint(q, confirmed[i]) for i, q in qmap.items()})
        except TimeoutError as e:
            raise HTTPException(503, str(e))
    if not resume_with_answers(run_id, {"answers": {} if body.skip else confirmed, "skip": body.skip, "notes": body.notes}):
        raise HTTPException(409, "This run is not waiting for answers.")
    return {"run_id": run_id, "status": "running"}


@app.get("/api/runs/{run_id}/answers-review")
def answers_review(run_id: str):
    """Every question this run asked, with the answer it used, who set it and the options, so the answers can be reviewed or changed.
    `reused` marks the ones taken from the saved glossary."""
    _check_id(run_id)
    data = _load_questions(run_id)
    ws = glossary.get_workspace(data.get("workspace", "default"))
    p, res = run_dir(run_id) / "answers.json", run_dir(run_id) / "result.json"
    given = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    qs = [q for q in data["questions"] if not q.get("auto")]
    final, source = clarify.resolve(qs, (given or {}).get("answers"), ws["answers"], skip=bool((given or {}).get("skip")))
    if res.exists():                                       # a finished run: what it actually used, even if the glossary has changed since
        used = {d["id"]: d for d in json.loads(res.read_text()).get("applied_definitions") or []}
        final, source = {i: used[i]["answer"] if i in used else final[i] for i in final}, {i: used[i]["source"] if i in used else source[i] for i in source}
    rows = []
    for q in qs:
        entry = ws["answers"].get(q["id"]) or {}
        rows.append({"id": q["id"], "text": q["text"], "options": [{"value": o["value"], "label": o["label"]} for o in q["options"]], "default": q["default"],
                     "answer": final[q["id"]], "source": source[q["id"]], "reused": source[q["id"]] == "glossary",
                     "saved": time.strftime("%Y-%m-%d", time.localtime(entry["updated"])) if source[q["id"]] == "glossary" and entry.get("updated") else ""})
    return {"workspace": data.get("workspace"), "status": (read_status(run_id) or {}).get("status"), "answers": rows}


class RerunAnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    ask_again: list[str] = Field(default_factory=list)


@app.post("/api/runs/{run_id}/rerun-answers")
def rerun_answers(run_id: str, body: RerunAnswersIn, request: Request):
    """Change answers of a finished run and re-run it from the cleaning step. A changed answer replaces the saved one; a question in `ask_again` is forgotten and asked again.
    Returns the id of the new run."""
    _check_id(run_id)
    _writable(run_id)
    guard.check_code(guard.code_from(request))
    data = _load_questions(run_id)
    if (read_status(run_id) or {}).get("status") not in ("done", "error"):
        raise HTTPException(409, "Only a finished run can be re-run with other answers.")
    if get_llm()[0] is not None:
        guard.reserve_ai_run()
    qmap = {q["id"]: q for q in data["questions"] if not q.get("auto")}
    for qid, val in body.answers.items():
        if qid not in qmap:
            raise HTTPException(400, f"Unknown question '{qid[:60]}'.")
        if val not in clarify.option_values(qmap[qid]):
            raise HTTPException(400, f"'{val[:60]}' is not an option for '{qid[:60]}'.")
    unknown = [i for i in body.ask_again if i not in qmap]
    if unknown:
        raise HTTPException(400, f"Unknown question '{unknown[0][:60]}'.")
    current = {r["id"]: r["answer"] for r in answers_review(run_id)["answers"]}
    final = {i: body.answers.get(i) or current[i] for i in qmap if i not in body.ask_again}
    ws_key = data.get("workspace", "default")
    try:
        glossary.merge_answers(ws_key, final, {i: q["text"] for i, q in qmap.items()}, labels={i: clarify.option_for(qmap[i], v)["label"] for i, v in final.items()},
                               fingerprints={i: clarify.fingerprint(qmap[i], v) for i, v in final.items()})
        glossary.delete_answers(ws_key, body.ask_again)
    except TimeoutError as e:
        raise HTTPException(503, str(e))
    return {"run_id": rerun_with_answers(run_id, final, body.ask_again), "status": "queued"}


class RemoveDerivedIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=10)


@app.post("/api/runs/{run_id}/remove-derived")
def remove_derived(run_id: str, body: RemoveDerivedIn, request: Request):
    """Re-run the analysis without the derived metrics the user removed. Returns the id of the new run."""
    _check_id(run_id)
    _writable(run_id)
    guard.check_code(guard.code_from(request))
    if get_llm()[0] is not None:
        guard.reserve_ai_run()
    res = run_dir(run_id) / "result.json"
    if (read_status(run_id) or {}).get("status") != "done" or not res.exists():
        raise HTTPException(409, "Only a finished run can be re-run.")
    made = {d["name"] for d in ((json.loads(res.read_text()).get("derived_metrics") or {}).get("created") or [])}
    unknown = [n for n in body.names if n not in made]
    if unknown:
        raise HTTPException(400, f"'{unknown[0][:60]}' is not a derived metric of this run.")
    return {"run_id": rerun_without_derived(run_id, body.names), "status": "queued"}


class GlossaryIn(BaseModel):
    answers: dict[str, dict | str] = Field(default_factory=dict)
    definitions: dict[str, str] = Field(default_factory=dict)
    notes: str = ""


def _glossary_scope(request: Request, response: Response | None = None) -> str:
    """The caller's own glossary (owner.py). A caller with no browser id yet has none: reads find nothing, and a save starts one (cookie on `response`)."""
    o = owner.current_owner(request, response)
    return (o or "no-owner")[:32]


@app.get("/api/glossary")
def get_glossary(request: Request, workspace: str | None = None):
    """Your saved definitions. No `workspace` lists your companies. Other visitors' are never shown."""
    with glossary.scoped(_glossary_scope(request)):
        return {"workspace": glossary.clean_key(workspace), **glossary.get_workspace(workspace)} if workspace is not None else {"workspaces": glossary.all_workspaces()}


@app.put("/api/glossary")
def put_glossary(body: GlossaryIn, request: Request, response: Response, workspace: str = "default"):
    entry = body.model_dump()
    err = glossary.validate_entry({**entry, "answers": {k: (v.get("value") if isinstance(v, dict) else v) for k, v in entry["answers"].items()}})
    if err:
        raise HTTPException(422, err)
    with glossary.scoped(_glossary_scope(request, response)):
        try:
            return {"workspace": glossary.clean_key(workspace), **glossary.put_workspace(workspace, entry)}
        except TimeoutError as e:
            raise HTTPException(503, str(e))


@app.delete("/api/glossary")
def delete_glossary(request: Request, workspace: str):
    with glossary.scoped(_glossary_scope(request)):
        try:
            if not glossary.delete_workspace(workspace):
                raise HTTPException(404, "No saved definitions for that workspace.")
        except TimeoutError as e:
            raise HTTPException(503, str(e))
    return {"deleted": glossary.clean_key(workspace)}


@app.get("/api/runs/{run_id}/data")
def view_data(run_id: str, source: str = "clean", offset: int = 0, limit: int = 50, q: str = "", sort: str = "", desc: bool = False):
    """Paginated view of the data the analysis used ('clean') or the table before cleaning ('raw'). Optional text search and sort."""
    import pandas as pd
    _check_id(run_id)
    if not valid_source(source):
        raise HTTPException(400, "source must be 'clean', 'raw' or 'tbl:<table name>'")
    p = source_path(run_dir(run_id), source)
    if p is None:
        raise HTTPException(404, "Data not ready")
    df = pd.read_pickle(p)
    if q.strip():
        mask = df.astype(str).apply(lambda col: col.str.contains(q.strip(), case=False, regex=False, na=False)).any(axis=1)
        df = df[mask]
    if sort and sort in df.columns:
        df = df.sort_values(sort, ascending=not desc, na_position="last")
    total = int(len(df))
    limit = max(1, min(limit, 200))
    page = df.iloc[max(0, offset): max(0, offset) + limit]
    return {"columns": [str(c) for c in df.columns], "total": total, "offset": max(0, offset), "limit": limit,
            "rows": [[_jsonable(v) for v in row] for row in page.itertuples(index=False, name=None)]}


@app.get("/api/runs/{run_id}/sources")
def data_sources(run_id: str):
    """All datasets of this run: each uploaded table as received, the joined table, and the cleaned table."""
    _check_id(run_id)
    return list_sources(run_dir(run_id))


@app.get("/api/runs/{run_id}/data/download")
def download_source(run_id: str, source: str = "clean"):
    import pandas as pd
    _check_id(run_id)
    if not valid_source(source):
        raise HTTPException(400, "source must be 'clean', 'raw' or 'tbl:<table name>'")
    p = source_path(run_dir(run_id), source)
    if p is None:
        raise HTTPException(404, "Data not ready")
    csv = (chr(0xFEFF) + pd.read_pickle(p).to_csv(index=False)).encode("utf-8")  # BOM so Excel reads non-English headers correctly
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", source.replace(":", "_"))
    return Response(csv, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@app.get("/api/runs/{run_id}/dashboard")
def dashboard_data(run_id: str):
    """Compact slice of the cleaned data plus a spec of what is worth plotting, for the interactive dashboard."""
    _check_id(run_id)
    wd = run_dir(run_id)
    res = wd / "result.json"
    if not res.exists() or not (wd / "clean.pkl").exists():
        raise HTTPException(404, "Dashboard not ready")
    return build_dashboard(wd, json.loads(res.read_text()), list((lensstore.get_view_state(wd) or {}).get("pinned") or []))


@app.get("/api/runs/{run_id}/dashboard/layout")
def dashboard_layout(run_id: str, importance: str = ""):
    """The tile sizes for the tiles the page can show now (a view edit may hide or fold some), so the grid still fills the window.
    `importance` is the comma-separated importance of each tile, in the page's order; the answer is one rectangle per tile, same order, id = position."""
    _check_id(run_id)
    try:
        vals = [float(p) for p in importance.split(",") if p][:dashplan.MAX_TILES]
    except ValueError:
        raise HTTPException(400, "importance must be a comma-separated list of numbers")
    return dashplan.arrange([{"id": str(i), "importance": v} for i, v in enumerate(vals)])


@app.get("/api/runs/{run_id}/data/compare")
def compare_data(run_id: str):
    """Per-column before/after summary so the user can see exactly what cleaning changed."""
    import pandas as pd
    _check_id(run_id)
    wd = run_dir(run_id)
    if not (wd / "raw.pkl").exists() or not (wd / "clean.pkl").exists():
        raise HTTPException(404, "Data not ready")
    raw, clean = pd.read_pickle(wd / "raw.pkl"), pd.read_pickle(wd / "clean.pkl")
    raw = raw.rename(columns=lambda c: str(c).strip())
    cols = []
    for c in list(dict.fromkeys(list(raw.columns) + list(clean.columns))):
        b, a = raw[c] if c in raw.columns else None, clean[c] if c in clean.columns else None
        row = {"name": str(c), "in_raw": b is not None, "in_clean": a is not None,
               "type_before": str(b.dtype) if b is not None else None, "type_after": str(a.dtype) if a is not None else None,
               "missing_before": int(b.isna().sum()) if b is not None else None, "missing_after": int(a.isna().sum()) if a is not None else None,
               "unique_after": int(a.nunique()) if a is not None else None}
        row["changed"] = bool(row["in_raw"] != row["in_clean"] or row["type_before"] != row["type_after"] or row["missing_before"] != row["missing_after"])
        cols.append(row)
    return {"rows_before": int(len(raw)), "rows_after": int(len(clean)), "columns": cols}


KEEPALIVE_S = 15.0


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str):
    _check_id(run_id)

    async def gen():
        idx, idle = 0, 0.0
        while True:
            evs = get_events(run_id, idx)
            if evs:
                idle = 0.0
            for e in evs:
                yield f"data: {json.dumps(e, default=str)}\n\n"
                idx += 1
            st = (read_status(run_id) or {}).get("status")
            if st in ("done", "error", "needs_input") and not get_events(run_id, idx):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.4)
            idle += 0.4
            if idle >= KEEPALIVE_S:          # a comment line: ignored by the browser, but it keeps a proxy from closing a quiet stream
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


class LensAskIn(BaseModel):
    question: str = Field(default="", max_length=300)
    chip: str | None = None
    choice: dict | None = None      # {"turn": <id of the clarifying turn>, "choice": <id of the option>}
    patch: dict | None = None       # {"turn": <id of an answer>, "set": {"chart": "line"}}: a chart control, applied by code with no AI
    focus: dict | None = None       # {"id": <item id>}: "Ask Lens about this": the question is answered with that item's context
    view_state: dict | None = None  # the sample run only: the browser's own view edits (the server never stores edits on the shared sample)
    tab: str = Field(default="", max_length=40)


class LensPinIn(BaseModel):
    turn: str
    target: str = Field(pattern="^(report|dashboard)$")


def _lens_run(run_id: str) -> tuple[Path, dict]:
    _check_id(run_id)
    if not lens.enabled(is_demo(run_id)):
        raise HTTPException(404, "Not found")
    res = run_dir(run_id) / "result.json"
    if (read_status(run_id) or {}).get("status") != "done" or not res.exists() or not (run_dir(run_id) / "clean.pkl").exists():
        raise HTTPException(409, "Questions can be asked once the report is ready.")
    return run_dir(run_id), json.loads(res.read_text(encoding="utf-8"))


def _wire_chart(c: dict | None, run_id: str) -> dict | None:
    return {**c, "url": f"/api/runs/{run_id}/files/{c['path']}" if c.get("path") else ""} if c else c


def _wire_turn(t: dict, run_id: str) -> dict:
    a = t["answer"]
    out = {**a, "chart": _wire_chart(a.get("chart"), run_id)}
    if a.get("how"):
        out["how"] = lenshow.with_lines(a["how"])          # the box is never empty, including for answers saved before it was written on the server
    return {**t, "answer": out}


def _wire_pin(p: dict, run_id: str) -> dict:
    return {**p, "chart": _wire_chart(p.get("chart"), run_id), **({"how": lenshow.with_lines(p["how"])} if p.get("how") else {})}


@app.get("/api/runs/{run_id}/lens")
def lens_info(run_id: str, tab: str = ""):
    """Lens for this run: whether it is on, whether an AI model is behind it, the suggested questions, the saved conversation and the pins."""
    _check_id(run_id)
    if not lens.enabled(is_demo(run_id)):
        return {"enabled": False, "ai": False, "chips": [], "history": [], "pins": [], "limit": {"asked": 0, "max": lens.max_questions()}}
    wd, result = _lens_run(run_id)
    ai = lens.ai_available() and not is_demo(run_id)
    out = lenschat.info(wd, result, is_demo(run_id), True, tab, ai)
    return {**out, "ai": lens.ai_available() and not is_demo(run_id), "history": [_wire_turn(t, run_id) for t in out["history"]], "pins": [_wire_pin(p, run_id) for p in out["pins"]]}


@app.post("/api/runs/{run_id}/lens/ask")
def lens_ask(run_id: str, body: LensAskIn, request: Request):
    """One question to Lens: a suggested question or chart control (no AI), or typed text (the AI picks a query, code checks, runs and draws it). Code computes every figure."""
    wd, result = _lens_run(run_id)
    demo = is_demo(run_id)
    typed = bool(body.question.strip()) and not (body.chip or body.choice or body.patch)
    allow = not demo
    capped = False
    if typed and allow and (lens_llm() is not None or public_demo()):
        guard.check_code(guard.code_from(request))           # typed questions use the AI: the code is needed (suggestions and chart controls never are)
        capped = not guard.under_cap("lens_questions")
        allow = not capped
    turn = lenschat.ask(wd, result, question=body.question, chip=body.chip, choice=body.choice, patch=body.patch, tab=body.tab,
                        llm=None if demo or capped else lens_llm(), allow_ai=allow, focus=body.focus, view_state=(body.view_state or {}) if demo else None)
    if capped and turn["answer"].get("status") == "unavailable":
        turn["answer"]["text"] = guard.LENS_MESSAGE
        turn["answer"]["status"] = "limit"
        lensstore.replace_turn(wd, turn)
    elif typed and turn["answer"].get("ai_calls"):
        guard.count("lens_questions")
    return _wire_turn(turn, run_id)


def lens_llm():
    return get_llm()[0]


@app.post("/api/runs/{run_id}/lens/pins")
def lens_pin(run_id: str, body: LensPinIn):
    """Pin an answer to the report page or the dashboard, in a section of its own. The report itself is not changed."""
    _writable(run_id)
    wd, _ = _lens_run(run_id)
    pin = lensstore.add_pin(wd, body.turn, body.target)
    if pin is None:
        raise HTTPException(404, "Only an answered question can be pinned.")
    return _wire_pin(pin, run_id)


class LensViewIn(BaseModel):
    turn: str


@app.post("/api/runs/{run_id}/lens/views")
def lens_save_view(run_id: str, body: LensViewIn):
    """Keep a short version of the report that Lens wrote as an alternative view. The original report is never changed."""
    _writable(run_id)
    wd, _ = _lens_run(run_id)
    v = lensreports.save_view(wd, body.turn)
    if v is None:
        raise HTTPException(404, "Only a summary can be kept as a short version.")
    return v


@app.delete("/api/runs/{run_id}/lens/views/short")
def lens_remove_view(run_id: str):
    _writable(run_id)
    wd, _ = _lens_run(run_id)
    if not lensstore.remove_view(wd, "short"):
        raise HTTPException(404, "No short version is saved.")
    return {"removed": "short"}


@app.delete("/api/runs/{run_id}/lens/pins/{pin_id}")
def lens_unpin(run_id: str, pin_id: str):
    _writable(run_id)
    wd, _ = _lens_run(run_id)
    if not lensstore.remove_pin(wd, pin_id):
        raise HTTPException(404, "Pin not found")
    return {"removed": pin_id}


# ------------------------------------------------------------------ view edits: hide, short view, order, titles, pins (presentation only; the saved report is never changed)
class ViewIn(BaseModel):
    command: dict | None = None
    commands: list[dict] | None = None
    state: dict | None = None          # only for the read-only sample run, whose edits live in the browser, never on the server


def _view_wire(run_id: str, wd: Path, result: dict, state: dict, message: str = "") -> dict:
    cat = viewedit.catalogue(result)
    res = viewedit.resolve(result, state, cat)
    charts = {}
    if res["chart"] and (wd / "clean.pkl").exists():
        df, ctx = lenschat.load_data(wd, result)
        from .lens import viewchart
        findings = {f.get("id"): f for f in result.get("findings") or []}
        for iid, ov in res["chart"].items():
            f = next((findings[i] for i in cat["items"][iid].get("finding_ids") or [] if i in findings), None)
            r = viewchart.render_override(wd, result, f, ov, df, ctx) if f else None
            if r:
                charts[iid] = {"url": f"/api/runs/{run_id}/files/{r['path']}", "note": r["note"]}
    return {"state": viewedit.normalise(state), "bar": viewedit.bar(result, state, cat), "resolved": res, "redrawn": charts, "message": message, "can_undo": bool(viewedit.normalise(state)["history"]),
            "items": {i: {k: v for k, v in it.items() if k in ("id", "tab", "kind", "title", "section", "score", "changeable", "card")} for i, it in cat["items"].items()},
            "sections": {t: x["sections"] for t, x in cat["tabs"].items()}}


@app.get("/api/runs/{run_id}/view")
def view_get(run_id: str):
    _check_id(run_id)
    wd = run_dir(run_id)
    p = wd / "result.json"
    if not p.exists():
        raise HTTPException(409, "This run has no report yet.")
    result = json.loads(p.read_text(encoding="utf-8"))
    state = {} if is_demo(run_id) else lensstore.get_view_state(wd)
    return {**_view_wire(run_id, wd, result, state), "local": is_demo(run_id)}


@app.post("/api/runs/{run_id}/view")
def view_apply(run_id: str, body: ViewIn):
    """One view change from the closed list (or a few, as one undoable step), by the hand controls. Chat uses the same code. An invalid id gets a plain sentence, and nothing changes."""
    _check_id(run_id)
    wd = run_dir(run_id)
    p = wd / "result.json"
    if not p.exists():
        raise HTTPException(409, "This run has no report yet.")
    result = json.loads(p.read_text(encoding="utf-8"))
    demo = is_demo(run_id)
    state = (body.state or {}) if demo else lensstore.get_view_state(wd)
    cmds = body.commands or ([body.command] if body.command else [])
    if not cmds:                  # nothing to change: just resolve the state given (the sample run's, kept in the browser) or the saved one
        return {**_view_wire(run_id, wd, result, state), "local": demo}
    try:
        new, msgs = viewedit.apply_many(result, state, cmds)
    except viewedit.ViewError as e:
        raise HTTPException(422, str(e))
    if not demo:
        lensstore.set_view_state(wd, new)
    return {**_view_wire(run_id, wd, result, new, " ".join(msgs)), "local": demo}


class BriefRenameIn(BaseModel):
    title: str = Field(max_length=90)


class BriefEditIn(BaseModel):
    section: str = Field(max_length=30)
    index: int | None = None
    text: str = Field(max_length=300)


def _brief_call(run_id: str, fn, *a):
    from .lens import briefs as lensbriefs
    _check_id(run_id)
    wd = run_dir(run_id)
    try:
        return fn(wd, *a)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs/{run_id}/briefs")
def briefs_list(run_id: str):
    _check_id(run_id)
    return {"briefs": lensstore.get_briefs(run_dir(run_id))}


@app.post("/api/runs/{run_id}/briefs/{brief_id}/rename")
def brief_rename(run_id: str, brief_id: str, body: BriefRenameIn):
    _check_id(run_id)
    _writable(run_id)
    from .lens import briefs as lensbriefs
    return _brief_call(run_id, lensbriefs.rename, brief_id, body.title)


@app.post("/api/runs/{run_id}/briefs/{brief_id}/edit")
def brief_edit(run_id: str, brief_id: str, body: BriefEditIn):
    _check_id(run_id)
    _writable(run_id)
    from .lens import briefs as lensbriefs
    return _brief_call(run_id, lensbriefs.edit, brief_id, body.section, body.index, body.text)


@app.delete("/api/runs/{run_id}/briefs/{brief_id}")
def brief_delete(run_id: str, brief_id: str):
    _check_id(run_id)
    _writable(run_id)
    from .lens import briefs as lensbriefs
    _brief_call(run_id, lensbriefs.delete, brief_id)
    return {"deleted": brief_id}


class FeedbackIn(BaseModel):
    turn: str = Field(max_length=40)
    vote: str = Field(max_length=8)


@app.post("/api/runs/{run_id}/lens/feedback")
def lens_feedback(run_id: str, body: FeedbackIn):
    """A thumbs up or down on one answer, recorded with the plan and the tools used (lens_feedback.jsonl in the run folder)."""
    from .lens import feedback
    _writable(run_id)
    wd, _ = _lens_run(run_id)
    line = feedback.record(wd, body.turn, body.vote)
    if line is None:
        raise HTTPException(400, "That answer or vote was not recognised.")
    return {"recorded": body.vote}


@app.get("/api/runs/{run_id}/export/{what}")
def export_file(run_id: str, what: str, brief: str = ""):
    """A PDF of a brief, all briefs (zip), the report or the short report, or the plain text of one. Made from the saved content, read-only."""
    from .lens import briefs as lensbriefs, exporter
    _check_id(run_id)
    wd = run_dir(run_id)
    p = wd / "result.json"
    if not p.exists():
        raise HTTPException(409, "This run has no report yet.")
    result = json.loads(p.read_text(encoding="utf-8"))
    b = lensbriefs.find(wd, brief) if brief else None
    if what in ("brief",) and not b:
        raise HTTPException(404, "That brief was not found.")
    try:
        data, ctype, name = exporter.make(what, result, wd, brief=b, briefs=lensstore.get_briefs(wd))
    except exporter.ExportError as e:
        raise HTTPException(503 if "browser" in str(e).lower() else 400, str(e))
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/runs/{run_id}/files/{path:path}")
def get_file(run_id: str, path: str):
    _check_id(run_id)
    root = run_dir(run_id).resolve()
    target = (root / path).resolve()
    allowed = target.parent == root / "charts" or target.parent == root / "powerbi" or target.name in ("report.md", "powerbi_pack.zip", "cleaned.csv")
    if root not in target.parents or not allowed or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)


@app.get("/api/runs/{run_id}/powerbi")
def download_pack(run_id: str):
    _check_id(run_id)
    z = run_dir(run_id) / "powerbi_pack.zip"
    if not z.exists():
        raise HTTPException(404, "Power BI pack not ready")
    return FileResponse(z, media_type="application/zip", filename=f"powerbi_pack_{run_id}.zip")


if FRONTEND_DIST.exists():  # single-process deployment: serve the built React app
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
