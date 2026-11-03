"""Tokens a run sends to the AI, by stage, counted with a fake model (no network, no key). The fake answers every call with an empty JSON object, so each stage takes its own repair path and then falls back to its rules:
the numbers are the calls a run makes and the size of what each one sends (about 4 characters a token), the same on every machine. Questions the run asks are answered with their defaults.

    python scripts/ai_tokens.py                  # the standard 12-column fixture and the Shopify-style fixture
    python scripts/ai_tokens.py --file <path>    # any table
The budget the test enforces is AI_TOKEN_BUDGET (default 40000 for the 12-column file)."""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
STANDARD = ROOT / "backend" / "tests" / "fixtures" / "standard_12_columns.csv"
SHOPIFY = ROOT / "backend" / "tests" / "fixtures" / "varied" / "a_shopify_orders.csv"


def make_standard() -> None:
    import numpy as np
    import pandas as pd
    g = np.random.RandomState(1)
    n = 400
    df = pd.DataFrame({"order_id": range(1000, 1000 + n), "order_date": pd.date_range("2024-01-01", periods=n, freq="12h").strftime("%Y-%m-%d"), "customer": [f"C{i % 90:03d}" for i in range(n)],
                       "region": g.choice(["North", "South", "East", "West"], n), "channel": g.choice(["Web", "Store", "Partner"], n), "category": g.choice(["Tops", "Shoes", "Bags", "Home"], n),
                       "status": g.choice(["paid", "pending", "refunded"], n, p=[.8, .1, .1]), "quantity": g.randint(1, 6, n), "unit_price": np.round(g.gamma(4, 300, n), 2), "revenue": 0.0,
                       "discount_pct": np.round(g.uniform(0, 25, n), 1), "notes": g.choice(["", "gift", "repeat"], n)})
    df["revenue"] = np.round(df.quantity * df.unit_price * (1 - df.discount_pct / 100), 2)
    STANDARD.write_text(df.to_csv(index=False), encoding="utf-8")


class Msg:
    def __init__(self, content):
        self.content = content


class EmptyJsonAI:
    """Answers every call with {}: valid JSON that no stage accepts, so every stage's repair and fallback run."""
    kwargs = {}

    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return Msg("{}")


def measure(path: Path, goal: str = "") -> dict:
    os.environ["LLM_PROVIDER"] = "none"
    tmp = tempfile.mkdtemp()
    os.environ.update(RUNS_DIR=tmp + "/runs", AI_CACHE_DIR=tmp + "/c", CHART_DPI="50", GLOSSARY_PATH=tmp + "/g.json")
    from app import graph, llm, runner
    fake = EmptyJsonAI()
    llm.get_llm = lambda: (fake, "fake")
    graph.get_llm = lambda: (fake, "fake")
    rid = runner.create_run(goal, [(path.name, path.read_bytes())])
    runner._execute(rid)
    wd = runner.run_dir(rid)
    if (runner.read_status(rid) or {}).get("status") == "needs_input":
        qs = json.loads((wd / "questions.json").read_text(encoding="utf-8"))["questions"]
        (wd / "answers.json").write_text(json.dumps({"answers": {q["id"]: q["default"] for q in qs}, "skip": False, "notes": ""}))
        runner._execute(rid)
    status = (runner.read_status(rid) or {}).get("status")
    u = json.loads((wd / "ai_usage.json").read_text(encoding="utf-8"))
    by: dict = collections.defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})
    for c in u["calls"]:
        s = by[c["stage"]]
        s["calls"] += 1
        s["input"] += c["input_tokens"]
        s["output"] += c["output_tokens"]
    t = u["totals"]
    return {"file": path.name, "status": status, "calls": t["calls"], "sent": t["sent"], "cached": t["cached"], "input": t["input_tokens"], "output": t["output_tokens"], "total": t["input_tokens"] + t["output_tokens"],
            "stages": dict(sorted(by.items(), key=lambda x: -(x[1]["input"] + x[1]["output"]))), "run_dir": str(wd)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not STANDARD.exists():
        make_standard()
    paths = [Path(a.file)] if a.file else [STANDARD, SHOPIFY]
    out = [measure(p) for p in paths]
    if a.json:
        print(json.dumps(out))
        return 0
    for r in out:
        print(f"{r['file']}: {r['calls']} calls ({r['cached']} from cache), {r['input']:,} input + {r['output']:,} output = {r['total']:,} tokens, run {r['status']}")
        for k, v in r["stages"].items():
            print(f"    {k:34s} {v['calls']:2d} calls {v['input'] + v['output']:7,d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
