"""Replay the Lens evaluation set and report plan accuracy by category.

    python scripts/lens_eval.py                      # (a) scripted fake planner: replies with each line's scripted plan. Checks that every expected plan is valid for the sample run and runs.
    python scripts/lens_eval.py --real               # (b) the real AI: Lens plans each request itself (needs an AI key; run it yourself, it makes one or two calls per request)
    python scripts/lens_eval.py --category custom --limit 10 --verbose

The set is backend/tests/lens_eval/requests.jsonl, one request per line: {id, category, request, tab, visible (item ids), expected (the tool plan), reply (the scripted plan), prior?, focus?}.
Each request is replayed on a fresh copy of the shipped sample run. Reported per category: PLAN accuracy (the tool sequence the planner chose equals the expected one, comparing the data_query
operation, the view commands, the number of briefs and the export kind) and RAN (every step of the plan ran without failing). Failures are printed. Exit code 1 if any request fails in fake mode."""
import argparse
import json
import os
import shutil
import sys
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("CHATBOT", "on")
os.environ.setdefault("AI_CACHE", "off")
os.environ.setdefault("CHART_DPI", "50")
DATA = ROOT / "backend" / "tests" / "lens_eval" / "requests.jsonl"
NEEDS_BRIEFS = {"export", "brief_manage"}


def load(path: Path = DATA) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def signature(step: dict) -> str:
    t, a = step.get("tool"), step.get("args") or {}
    if t == "data_query":
        return "data_query/" + str((a.get("query") or {}).get("op"))
    if t == "view_edit":
        return "view_edit/" + "+".join(str(c.get("cmd")) for c in a.get("commands") or [] if isinstance(c, dict))
    if t == "brief":
        return f"brief/{len(a.get('briefs') or [])}"
    if t == "data_catalog":
        return f"data_catalog/{a.get('lookup') or 'find'}"
    if t in ("export", "brief_manage"):
        return f"{t}/{a.get('what') or a.get('op')}"
    if t == "custom_analysis":
        return "custom_analysis"
    return str(t)


class Scripted:
    """The scripted fake planner: replies with the next scripted reply, and keeps the prompts."""
    kwargs = {}

    def __init__(self, *replies):
        self.replies, self.prompts, self.calls = list(replies), [], 0

    def invoke(self, messages):
        self.calls += 1
        self.prompts.append(messages[1][1])
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

        class Msg:
            content = json.dumps(r)
        return Msg()


def fresh_run():
    from app import runner
    from app.config import DEMO_DIR
    rid = uuid.uuid4().hex[:12]
    wd = runner.run_dir(rid)
    shutil.copytree(DEMO_DIR, wd)
    res = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    res["run_id"] = rid
    (wd / "result.json").write_text(json.dumps(res), encoding="utf-8")
    runner.write_status(rid, "done", goal="eval")
    return rid, wd, res


def replay(lines: list[dict], real: bool = False, verbose: bool = False) -> list[dict]:
    from app import llm as llmmod
    from app.lens import chat, store
    rid, wd, res = fresh_run()
    real_llm = llmmod.get_llm()[0] if real else None
    if real and real_llm is None:
        raise SystemExit("No AI model is configured (set GROQ_API_KEY or another provider). The scripted mode needs none.")
    out = []
    for line in lines:
        (wd / "lens.json").unlink(missing_ok=True)
        for f in ("lens_unmet.jsonl", "lens_feedback.jsonl"):
            (wd / f).unlink(missing_ok=True)
        chat._DAILY["until"] = 0.0
        if line["category"] in NEEDS_BRIEFS or line.get("setup") == "briefs":
            plan = {"plan": [{"tool": "brief", "args": {"briefs": [{"audience": "the board", "length": "short", "focus": "", "columns": [], "evidence": []}, {"audience": "the shop team", "length": "short", "focus": "", "columns": [], "evidence": []}]}}]}
            chat.ask(wd, res, question="make briefs", llm=Scripted(plan), allow_ai=True)
        if line.get("prior"):
            chat.ask(wd, res, question=line["prior"]["request"], llm=Scripted(line["prior"]["reply"]) if not real else real_llm, allow_ai=True, tab=line["tab"])
        llm = real_llm if real else Scripted(line["reply"])
        try:
            t = chat.ask(wd, res, question=line["request"], llm=llm, allow_ai=True, tab=line["tab"], focus={"id": line["focus"]} if line.get("focus") else None)
            a = t["answer"]
            ag = a.get("agent") or {}
            planned = [signature(s) for s in ag.get("plan") or []]
            ran = [m for m in ag.get("steps") or []]
            expected = [signature({"tool": tl, "args": ar}) for tl, ar in line["expected"]]
            ok_plan = planned == expected
            ok_ran = ok_plan and all(m["ok"] for m in ran) and len(ran) == len(expected) if expected else (a.get("status") in ("no_fit", "answered") and not ag.get("plan"))
            rec = {"id": line["id"], "category": line["category"], "request": line["request"], "plan_ok": ok_plan, "ran_ok": bool(ok_ran), "expected": expected, "planned": planned, "status": a.get("status"),
                   "text": str(a.get("text"))[:160], "calls": getattr(llm, "calls", 0)}
        except Exception as e:           # noqa: BLE001  a crash is a failure of that request, not of the run
            rec = {"id": line["id"], "category": line["category"], "request": line["request"], "plan_ok": False, "ran_ok": False, "expected": [], "planned": [], "status": "error", "text": f"{type(e).__name__}: {e}"[:160], "calls": 0}
        out.append(rec)
        if verbose:
            print(("ok  " if rec["plan_ok"] and rec["ran_ok"] else "FAIL"), rec["id"], rec["category"], rec["request"])
    return out


def report(results: list[dict]) -> int:
    by = defaultdict(list)
    for r in results:
        by[r["category"]].append(r)
    print(f"{'category':14s} {'n':>4s} {'plan':>8s} {'ran':>8s}")
    for c, rs in sorted(by.items()):
        n = len(rs)
        print(f"{c:14s} {n:4d} {sum(r['plan_ok'] for r in rs) / n:8.0%} {sum(r['ran_ok'] for r in rs) / n:8.0%}")
    n = len(results)
    print(f"{'ALL':14s} {n:4d} {sum(r['plan_ok'] for r in results) / n:8.0%} {sum(r['ran_ok'] for r in results) / n:8.0%}")
    bad = [r for r in results if not (r["plan_ok"] and r["ran_ok"])]
    if bad:
        print(f"\n{len(bad)} failure(s):")
        for r in bad:
            print(f"- {r['id']} [{r['category']}] “{r['request']}”\n    expected {r['expected']}\n    planned  {r['planned']}   status {r['status']}: {r['text']}")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="use the real AI instead of the scripted planner")
    ap.add_argument("--category", action="append")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--file", type=Path, default=DATA)
    a = ap.parse_args(argv)
    lines = load(a.file)
    if a.category:
        lines = [x for x in lines if x["category"] in a.category]
    if a.limit:
        lines = lines[: a.limit]
    results = replay(lines, real=a.real, verbose=a.verbose)
    code = report(results)
    return 0 if a.real else code


if __name__ == "__main__":
    sys.exit(main())
