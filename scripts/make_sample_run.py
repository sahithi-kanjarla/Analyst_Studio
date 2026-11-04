"""Rebuild the demo sample run (sample_data/demo/sales_run) from the five demo files, with the REAL AI.

    GROQ_API_KEY=... python scripts/make_sample_run.py [--goal "..."] [--timeout-min 40]

What it does
  1. Uploads sample_data/demo/files/* through the real pipeline (runner.create_run / start_run, the same code
     POST /api/runs uses). The key must already be in the environment; it is never read from a file here,
     never printed, never written. A throw-away data folder is used, so no saved glossary or AI cache leaks in.
  2. Waits for the run to finish (clarifying questions are skipped = the defaults).
  3. Copies the finished run into the TEMP folder sample_data/demo/_new_run, stripping anything private
     (owner hash, local paths, key text).
  4. Runs scripts/check_sample_run.py on it and prints its table, the token usage, the AI mode and any stage
     that fell back.
  5. ONLY if every must_show figure passes, every stage ran and the AI mode is "real": replaces
     sample_data/demo/sales_run with the temp run and deletes the temp folder.
     Otherwise the temp folder is kept, the old sample is left alone, and what failed is printed.
It never commits and never runs the test suite.
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "sample_data" / "demo"
FILES = DEMO / "files"
SAMPLE = DEMO / "sales_run"
TEMP = DEMO / "_new_run"
EXPECTED = DEMO / "expected.json"
TEXT_EXT = {".json", ".jsonl", ".md", ".csv", ".txt", ".tsv", ".pbit", ".m", ".dax", ".tmdl"}
SKIP_COPY = {"inputs", "answers.json", "questions.json"}     # the uploads and the question state are not part of a shipped sample
DEMO_GOAL = "Find what drives revenue and returns, and what to do about it."
KEY_PATTERN = re.compile(r"gsk_[A-Za-z0-9]{10,}|AIza[0-9A-Za-z_\-]{20,}")


def die(msg: str, code: int = 2):
    print(msg)
    sys.exit(code)


def private_strings(extra: list[str]) -> list[str]:
    """Literal path texts to remove: this repo, the throw-away data folder, the home folder (all spellings)."""
    out = []
    for p in [ROOT, Path.home(), *map(Path, extra)]:
        s = str(p)
        for v in {s, s.replace("\\", "/"), s.replace("\\", "\\\\"), s.lower(), s.replace("\\", "/").lower()}:
            if len(v) > 3:
                out.append(v)
    return sorted(set(out), key=len, reverse=True)


def clean_text(text: str, paths: list[str], key: str) -> str:
    for p in paths:
        text = text.replace(p, "<path>")
    if key:
        text = text.replace(key, "<removed>")
    return KEY_PATTERN.sub("<removed>", text)


def strip_private(obj):
    """Drop the owner hash wherever it is stored (status.json, result.json)."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if k not in ("owner", "owner_hash", "owner_token")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def copy_sanitised(src: Path, dst: Path, paths: list[str], key: str) -> list[str]:
    """Copy the run folder; text files are cleaned, binary files are only scanned. Returns problems that could not be cleaned."""
    leaks = []
    dst.mkdir(parents=True)
    for item in sorted(src.iterdir()):
        if item.name in SKIP_COPY:
            continue
        target = dst / item.name
        if item.is_dir():
            leaks += copy_sanitised(item, target, paths, key)
            continue
        if item.suffix.lower() in TEXT_EXT:
            text = item.read_text(encoding="utf-8", errors="replace")
            if item.suffix.lower() == ".json":
                try:
                    text = json.dumps(strip_private(json.loads(text)), default=str, **({"indent": 1} if "\n" in text.strip() else {}))
                except ValueError:
                    pass
            text = clean_text(text, paths, key)
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(item, target)
            blob = item.read_bytes()
            for p in paths:
                if p.encode() in blob or p.encode("utf-16-le") in blob:
                    leaks.append(f"{item.name}: contains a local path")
                    break
            if (key and key.encode() in blob) or KEY_PATTERN.search(blob.decode("latin-1")):
                leaks.append(f"{item.name}: contains key-like text")
    if (dst / "status.json").exists():     # status.json keeps only what the page needs
        st = json.loads((dst / "status.json").read_text(encoding="utf-8"))
        st["status"] = "done"
        (dst / "status.json").write_text(json.dumps(st), encoding="utf-8")
    return leaks


def wait_done(runner, run_id: str, timeout_s: float) -> dict:
    t0, answered = time.time(), False
    while time.time() - t0 < timeout_s:
        st = runner.read_status(run_id) or {}
        if st.get("status") in ("done", "error"):
            return st
        if st.get("status") == "needs_input" and not answered:
            answered = runner.resume_with_answers(run_id, {"answers": {}, "skip": True, "notes": ""})   # "skip" = use the defaults
            print("   the run asked clarifying questions: used the defaults")
        time.sleep(3)
    return {"status": "timeout"}


def judge(run: Path) -> dict:
    """AI mode, stages that fell back, and whether every stage ran, from the saved files only."""
    res = json.loads((run / "result.json").read_text(encoding="utf-8"))
    sources = res.get("stage_sources") or {}
    # a stage that makes no AI call BY DESIGN says so in its decision log (e.g. the brief on an exploratory run with no KPI and no description): that is not a fallback
    by_design = {k for k in sources for d in res.get("decisions") or [] if isinstance(d, dict) and "no AI call was made" in str(d.get("why", ""))
                 and (k in str(d.get("decision", "")).lower() or (k == "brief" and "brief" in str(d.get("decision", "")).lower()))}
    ai_values = ("llm", "ai", "repaired", "mixed")                   # the AI wrote it, possibly after a repair, or part of it was reworded by rules
    llm_stages = sorted(k for k, v in sources.items() if str(v).lower() in ai_values)
    fell_back = sorted(k for k, v in sources.items() if str(v).lower() == "fallback")
    fell_back += [str(d.get("decision")) for d in res.get("decisions") or [] if isinstance(d, dict) and str(d.get("decision", "")).startswith("AI unavailable")]
    rules_only = sorted(k for k, v in sources.items() if str(v).lower() in ("rules", "rules-only") and k not in by_design)
    if sources and not llm_stages:
        mode = "rules-only"
    elif fell_back or rules_only or not sources:
        mode = "partly"
    else:
        mode = "real"
    failed = [str(w.get("text") if isinstance(w, dict) else w) for w in res.get("warnings") or [] if "fail" in str(w).lower() or "skipped" in str(w).lower()]
    failed += [str(d.get("decision")) for d in res.get("decisions") or [] if isinstance(d, dict) and str(d.get("decision", "")).startswith("stage failed")]
    missing = [k for k in ("report_md", "findings", "cleaning_log", "key_metrics", "profile", "data_model", "plan", "critique", "powerbi") if not res.get(k)]
    usage = {}
    try:
        usage = json.loads((run / "ai_usage.json").read_text(encoding="utf-8")).get("totals", {})
    except (OSError, ValueError):
        pass
    return {"mode": mode, "by_design": sorted(by_design), "fell_back": sorted(set(fell_back)), "rules_stages": rules_only, "failed": failed, "missing": missing, "usage": usage, "llm_mode": res.get("llm_mode"), "sources": sources}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", default=DEMO_GOAL, help='the demo goal; pass --goal "" for an exploratory analysis instead')
    ap.add_argument("--company", default="Sample Store")
    ap.add_argument("--industry", default="retail")
    ap.add_argument("--timeout-min", type=float, default=40)
    a = ap.parse_args()

    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        die("GROQ_API_KEY is not set in the environment. Set it for this command only (the script never reads .env and never prints the key), then run again.")
    if TEMP.exists():
        die(f"{TEMP} already exists from an earlier attempt. Look at it, then delete it and run again.")
    files = sorted(p for p in FILES.iterdir() if p.is_file())
    if not files:
        die(f"No demo files in {FILES}.")

    # a throw-away data folder: no saved glossary answers, AI cache or daily counts from other runs; set BEFORE the app is imported
    scratch = Path(tempfile.mkdtemp(prefix="make_sample_run_"))
    os.environ.update({"DATA_DIR": str(scratch), "LLM_PROVIDER": "groq", "PUBLIC_DEMO": "off"})
    os.environ.pop("RUNS_DIR", None)
    sys.path.insert(0, str(ROOT / "backend"))
    from app import llm as llm_mod, runner

    llm, label = llm_mod.get_llm()
    if llm is None:
        die(f"No real AI model available ({label}). Nothing was made.")
    print(f"AI model: {label}")
    from app.context import normalise_context
    ctx = normalise_context({"company": a.company, "industry": a.industry, "explore": not a.goal.strip()})
    run_id = runner.create_run(a.goal, [(p.name, p.read_bytes()) for p in files], context=ctx)
    print(f"Uploaded {len(files)} files, run started ({time.strftime('%H:%M:%S')}) ...")
    runner.start_run(run_id)
    st = wait_done(runner, run_id, a.timeout_min * 60)
    if st.get("status") != "done":
        die(f"The run did not finish: status={st.get('status')}, error={str(st.get('error'))[:300]}. Run folder kept in the throw-away data folder: {scratch}", 1)
    src = runner.run_dir(run_id)

    leaks = copy_sanitised(src, TEMP, private_strings([str(scratch)]), key)
    print(f"Finished; copied to {TEMP.relative_to(ROOT)}")

    # the checker's own table
    from importlib import util
    spec = util.spec_from_file_location("check_sample_run", ROOT / "scripts" / "check_sample_run.py")
    chk = util.module_from_spec(spec)
    spec.loader.exec_module(chk)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = chk.run(TEMP / "result.json", EXPECTED)
    print(buf.getvalue())

    j = judge(TEMP)
    u = j["usage"]
    print(f"Tokens: {u.get('input_tokens', 0):,} in / {u.get('output_tokens', 0):,} out, {u.get('calls', 0)} calls sent, {u.get('cached', 0)} cached"
          f"{' (estimated)' if u.get('estimated') else ''}, {u.get('seconds', 0)} s in AI calls")
    print(f"AI mode: {j['mode']} ({j['llm_mode']}); fell back: {', '.join(j['fell_back']) or 'none'}; rules stages: {', '.join(j['rules_stages']) or 'none'}; rules by design (no AI call needed): {', '.join(j['by_design']) or 'none'}")

    problems = []
    if code != 0:
        problems.append("check_sample_run.py FAILED (see table)")
    if j["mode"] != "real":
        problems.append(f"AI mode is {j['mode']}, not real")
    if j["failed"]:
        problems.append("stage(s) failed or were skipped: " + "; ".join(j["failed"])[:300])
    if j["missing"]:
        problems.append("missing in result.json: " + ", ".join(j["missing"]))
    if leaks:
        problems.append("private text left in binary files: " + "; ".join(leaks))
    if problems:
        print("\nNOT REPLACED. Old sample left alone; the new run is kept in " + str(TEMP.relative_to(ROOT)))
        for p in problems:
            print("  -", p)
        sys.exit(1)

    old = SAMPLE.with_name("_old_sales_run")
    SAMPLE.rename(old)
    try:
        TEMP.rename(SAMPLE)
    except OSError:
        old.rename(SAMPLE)
        raise
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"\nREPLACED {SAMPLE.relative_to(ROOT)} with the new run; temp folder removed. Nothing committed.")


if __name__ == "__main__":
    main()
