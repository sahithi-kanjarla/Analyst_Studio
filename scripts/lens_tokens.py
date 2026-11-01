"""Estimated input tokens of the planning call for a few scripted requests, by part, before (everything sent, as the agent did at first) and after (only what the request could use). No AI is called: the fake AI
replies from tests/lens_eval/requests.jsonl, the real DuckDB and the sample run are used, and sizes are estimated at about 4 characters a token (the same estimate ai_usage.json uses without a provider count).
  python scripts/lens_tokens.py                      # the 5 representative requests
  python scripts/lens_tokens.py --ids e001,e021      # others"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

REPRESENTATIVE = ["e001", "e021", "e047", "e077", "e099"]
PARTS = ["system_text", "data_card", "fact_sheet", "item_list", "history", "other"]


def measure(ids: list[str], full: bool) -> dict[str, dict]:
    import lens_eval
    from app import llm as llmmod
    from app.lens import chat
    from app.lens.agent import context, planner
    lines = {x["id"]: x for x in lens_eval.load()}
    rid, wd, res = lens_eval.fresh_run()
    orig, orig_sys = context.build, planner.system_prompt
    planner.system_prompt = lambda env=None, full_=full, **k: orig_sys(env, full=full_ or k.get("full", False))
    context.build = lambda env, tools_help="", full_=full, **k: orig(env, tools_help, full=full_ or k.get("full", False))
    out = {}
    try:
        for i in ids:
            line = lines[i]
            (wd / "lens.json").unlink(missing_ok=True)
            chat._DAILY["until"] = 0.0
            if line.get("prior"):
                chat.ask(wd, res, question=line["prior"]["request"], llm=lens_eval.Scripted(line["prior"]["reply"]), allow_ai=True, tab=line["tab"])
            chat.ask(wd, res, question=line["request"], llm=lens_eval.Scripted(line["reply"]), allow_ai=True, tab=line["tab"], focus={"id": line["focus"]} if line.get("focus") else None)
            plan = [c for c in llmmod.usage_calls() if c.get("input_by_part") and not c["cache_hit"]]
            first = plan[0] if plan else {"input_tokens": 0, "input_by_part": {}}
            out[i] = {"request": line["request"], "total": first["input_tokens"], "parts": first.get("input_by_part", {})}
    finally:
        context.build, planner.system_prompt = orig, orig_sys
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=",".join(REPRESENTATIVE))
    a = ap.parse_args(argv)
    ids = a.ids.split(",")
    before, after = measure(ids, True), measure(ids, False)
    print(f"{'request':44s} {'before':>7s} {'after':>7s}   " + "  ".join(f"{p[:9]:>9s}" for p in PARTS))
    tb = ta = 0
    for i in ids:
        b, f = before[i], after[i]
        tb, ta = tb + b["total"], ta + f["total"]
        print(f"{b['request'][:44]:44s} {b['total']:7d} {f['total']:7d}   " + "  ".join(f"{b['parts'].get(p, 0):4d}>{f['parts'].get(p, 0):<4d}" for p in PARTS))
    print(f"{'average per request':44s} {tb // len(ids):7d} {ta // len(ids):7d}   (cut {1 - ta / max(tb, 1):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
