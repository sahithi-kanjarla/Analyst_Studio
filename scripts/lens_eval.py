"""Replay the Lens evaluation set and report plan accuracy by category.

    python scripts/lens_eval.py                      # (a) scripted fake planner: replies with each line's scripted plan. Checks that every expected plan is valid for the sample run and runs.
    python scripts/lens_eval.py --real               # (b) the real AI: Lens plans each request itself (needs an AI key; run it yourself, it makes one or two calls per request)
    python scripts/lens_eval.py --category custom --limit 10 --verbose

The set is backend/tests/lens_eval/requests.jsonl, one request per line: {id, category, request, tab, visible (item ids), expected (the tool plan), reply (the scripted plan), prior?, focus?}.
Each request is replayed on a fresh copy of the shipped sample run. Three things are scored and reported separately, per category:
  PLAN     the tool sequence the planner chose equals the expected plan or one of the acceptable alternatives (a line may list several valid plans: "acceptable"; "several_valid" marks it)
  OUTCOME  what the person gets is right whatever plan produced it: the expected kind of result (a chart, a table, a refusal, a view change, a brief, an export, a question, text), every figure in the text
           traceable to a computed value or to the report's own records, and no internal term in anything shown
  RAN      every step of the plan ran without failing
  python scripts/lens_eval.py --sample 20 --list          # print 20 requests chosen round-robin across the categories, without calling any AI
Failures are printed. Exit code 1 if any request fails in fake mode."""
import argparse
import json
import os
import re
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


FORBIDDEN = re.compile(r"\b(?:view_edit|data_query|custom_analysis|report_facts|chart_edit|brief_manage|premise_check|recheck_chart|general_knowledge|data_catalog|why_breakdown|registry|json)\b|\b[a-z]+(?:_[a-z]+)+\b", re.I)
NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def numbers_of(text: str) -> set[str]:
    t = re.sub(r"\b(?:19|20)\d\d(?:-(?:Q[1-4]|\d{2}(?:-\d{2})?))?\b|\bQ[1-4]\b", " ", str(text))
    return {m.group(0).replace(",", "").rstrip(".") for m in NUM.finditer(t)}


def untraceable_numbers(a: dict, result: dict, question: str) -> list[str]:
    """Numbers in what the person reads that match no computed value (a table cell, a recorded figure, a count of rows) and no text of the report's own records."""
    from app.lens import records
    allowed = set()
    for r in (a.get("table") or {}).get("shown") or []:
        for c in r:
            allowed |= numbers_of(c)
    for f in a.get("figures") or []:
        allowed |= numbers_of(f.get("shown", "")) | {str(f.get("value"))}
    allowed |= numbers_of(question) | numbers_of(" ".join(str(x) for x in (a.get("how") or {}).get("lines") or []))
    allowed |= {str((a.get("table") or {}).get("total_rows", "")), str(len((a.get("table") or {}).get("rows") or []))}
    allowed |= numbers_of(json.dumps(result.get("report_view") or {}, default=str)) | numbers_of(json.dumps(result.get("strategies") or [], default=str)) | numbers_of(json.dumps(result.get("cleaning_log") or [], default=str))
    allowed |= numbers_of(json.dumps(result.get("applied_definitions") or [], default=str)) | numbers_of(json.dumps((result.get("profile") or {}), default=str)) | numbers_of(json.dumps(result.get("summary_facts") or {}, default=str))
    shown = " ".join([str(a.get("text") or "")] + [str(x) for x in a.get("lines") or []] + [str(x) for x in a.get("actions") or []])
    norm = {n.lstrip("0") or "0" for n in allowed} | allowed
    return sorted(n for n in numbers_of(shown) if n not in allowed and (n.lstrip("0") or "0") not in norm and not any(abs(float(n) - float(x)) < 0.051 for x in allowed if re.fullmatch(r"\d+(?:\.\d+)?", x) and re.fullmatch(r"\d+(?:\.\d+)?", n)))


def detected_kind(a: dict) -> str:
    v = a.get("view") or {}
    if v.get("changed") or v.get("preview"):
        return "view_change"
    if a.get("briefs"):
        return "brief"
    if a.get("export"):
        return "export"
    if a.get("status") == "clarify":
        return "question"
    if a.get("status") in ("no_fit", "unavailable", "limit") or a.get("unmet"):
        return "refusal"
    if (a.get("chart") or {}).get("path"):
        return "chart"
    if a.get("table"):
        return "table"
    return "text"


def outcome_check(a: dict, line: dict, result: dict) -> dict:
    want = (line.get("outcome") or {}).get("kinds") or []
    got = detected_kind(a)
    kind_ok = (not want) or got in want or (got == "table" and "chart" in want) or (got == "chart" and "table" in want)
    # a brief's figures are checked by the brief's own cited-fact verifier, a refusal states none, so those two are not re-scored here
    bad_numbers = untraceable_numbers(a, result, line["request"]) if (line.get("outcome") or {}).get("figures", True) and got not in ("refusal", "brief") else []
    shown = "\n".join([str(a.get("text") or ""), str(a.get("label") or "")] + [str(x) for x in a.get("lines") or []] + [str(x) for x in (a.get("how") or {}).get("lines") or []])
    internal = sorted({m.group(0) for m in FORBIDDEN.finditer(shown)}) if (line.get("outcome") or {}).get("no_internal", True) else []
    internal = [x for x in internal if x.lower() not in ("order_date", "order_id", "discount_pct")]
    return {"kind": got, "kind_ok": kind_ok, "figures_ok": not bad_numbers, "bad_numbers": bad_numbers, "internal_ok": not internal, "internal": internal, "ok": kind_ok and not bad_numbers and not internal}


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
    if t == "why_breakdown":
        return "why_breakdown"
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
            allowed = [expected] + [[signature({"tool": tl, "args": ar}) for tl, ar in alt] for alt in line.get("acceptable") or []]
            ok_plan = planned in allowed
            ok_ran = ok_plan and all(m["ok"] for m in ran) and len(ran) == len(planned) if planned else (a.get("status") in ("no_fit", "answered") and not ag.get("plan"))
            oc = outcome_check(a, line, res)
            rec = {"id": line["id"], "category": line["category"], "request": line["request"], "plan_ok": ok_plan, "ran_ok": bool(ok_ran), "outcome_ok": oc["ok"], "outcome": oc, "expected": expected, "planned": planned,
                   "status": a.get("status"), "text": str(a.get("text"))[:160], "calls": getattr(llm, "calls", 0), "several_valid": bool(line.get("acceptable"))}
            for alt in line.get("acceptable") or []:                      # every acceptable plan must itself run and give an acceptable outcome (checked with the scripted planner)
                if not real:
                    (wd / "lens.json").unlink(missing_ok=True)
                    t2 = chat.ask(wd, res, question=line["request"], llm=Scripted({"plan": [{"tool": tl, "args": ar} for tl, ar in alt]}), allow_ai=True, tab=line["tab"])
                    oc2 = outcome_check(t2["answer"], line, res)
                    if not oc2["ok"] or not all(m["ok"] for m in (t2["answer"].get("agent") or {}).get("steps") or []):
                        rec["outcome_ok"], rec["outcome"] = False, {**oc2, "alternative_failed": [signature({"tool": tl, "args": ar}) for tl, ar in alt]}
        except Exception as e:           # noqa: BLE001  a crash is a failure of that request, not of the run
            rec = {"id": line["id"], "category": line["category"], "request": line["request"], "plan_ok": False, "ran_ok": False, "outcome_ok": False, "outcome": {}, "expected": [], "planned": [], "status": "error", "text": f"{type(e).__name__}: {e}"[:160], "calls": 0}
        out.append(rec)
        if verbose:
            print(("ok  " if rec["plan_ok"] and rec["ran_ok"] else "FAIL"), rec["id"], rec["category"], rec["request"])
    return out


def report(results: list[dict]) -> int:
    by = defaultdict(list)
    for r in results:
        by[r["category"]].append(r)
    print(f"{'category':14s} {'n':>4s} {'plan':>7s} {'outcome':>8s} {'ran':>6s}")
    for c, rs in sorted(by.items()):
        n = len(rs)
        print(f"{c:14s} {n:4d} {sum(r['plan_ok'] for r in rs) / n:7.0%} {sum(r['outcome_ok'] for r in rs) / n:8.0%} {sum(r['ran_ok'] for r in rs) / n:6.0%}")
    n = len(results)
    print(f"{'ALL':14s} {n:4d} {sum(r['plan_ok'] for r in results) / n:7.0%} {sum(r['outcome_ok'] for r in results) / n:8.0%} {sum(r['ran_ok'] for r in results) / n:6.0%}")
    sv = [r for r in results if r.get("several_valid")]
    if sv:
        print(f"({len(sv)} request(s) have several valid plans)")
    bad = [r for r in results if not (r["plan_ok"] and r["ran_ok"] and r["outcome_ok"])]
    if bad:
        print(f"\n{len(bad)} failure(s):")
        for r in bad:
            why = []
            if not r["plan_ok"]:
                why.append("plan")
            if not r["outcome_ok"]:
                why.append("outcome")
            if not r["ran_ok"]:
                why.append("ran")
            oc = r.get("outcome") or {}
            print(f"- {r['id']} [{r['category']}] “{r['request']}”  ({', '.join(why)})\n    expected {r['expected']}\n    planned  {r['planned']}   status {r['status']}: {r['text']}\n    outcome  got {oc.get('kind')}, "
                  f"untraceable numbers {oc.get('bad_numbers')}, internal terms {oc.get('internal')}")
    return 1 if bad else 0


def choose(lines: list[dict], n: int | None) -> list[dict]:
    """n requests, round-robin across the categories (in the order the file lists them), so a small sample still covers every kind of request."""
    if not n or n >= len(lines):
        return lines
    cats: dict[str, list[dict]] = {}
    for x in lines:
        cats.setdefault(x["category"], []).append(x)
    out, i = [], 0
    while len(out) < n:
        took = False
        for c in cats:
            if i < len(cats[c]) and len(out) < n:
                out.append(cats[c][i])
                took = True
        if not took:
            break
        i += 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="use the real AI instead of the scripted planner")
    ap.add_argument("--category", action="append")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int, help="N requests, round-robin across the categories")
    ap.add_argument("--list", action="store_true", help="print the chosen requests and stop (no AI is called)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--file", type=Path, default=DATA)
    a = ap.parse_args(argv)
    lines = load(a.file)
    if a.category:
        lines = [x for x in lines if x["category"] in a.category]
    lines = choose(lines, a.sample)
    if a.limit:
        lines = lines[: a.limit]
    if a.list:
        for x in lines:
            print(f"{x['id']}  {x['category']:13s} [{x['tab']}] {x['request']}" + ("   (several valid plans)" if x.get("acceptable") else ""))
        print(f"{len(lines)} request(s); no AI was called.")
        return 0
    results = replay(lines, real=a.real, verbose=a.verbose)
    code = report(results)
    return 0 if a.real else code


if __name__ == "__main__":
    sys.exit(main())
