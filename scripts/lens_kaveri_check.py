"""Real-AI check of Lens on the Kaveri run: eight questions (two follow-ups, two chart-control requests). For each: the question, the answer, and whether the figure matches
what pandas gives directly on the cleaned data (computed here, not by Lens).

    python scripts/lens_kaveri_check.py [run_id]       # default: the newest finished Kaveri run in backend/data/runs

Needs a configured AI (GROQ_API_KEY in .env). Works on a copy of the run, so the run itself is not changed."""
import json, os, shutil, sys, tempfile
from pathlib import Path

os.environ["AI_CACHE"] = "off"       # every question really goes to the model
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import config, llm  # noqa: E402,F401  (loads .env)
from app.lens import chat  # noqa: E402
import pandas as pd  # noqa: E402

RUNS = ROOT / "backend" / "data" / "runs"


def find_run(arg: str | None) -> Path:
    if arg:
        return RUNS / arg
    best = None
    for d in RUNS.glob("*/result.json"):
        r = json.loads(d.read_text(encoding="utf-8"))
        if "Which customer tier" in (r.get("goal") or "") and (r.get("understanding") or {}).get("source") == "llm" and (d.parent / "clean.pkl").exists():
            if best is None or d.stat().st_mtime > best.stat().st_mtime:
                best = d
    if best is None:
        sys.exit("No finished Kaveri run with an AI understanding record found; run scripts/scorecard.py first.")
    return best.parent


def rows(t):
    return t["answer"].get("table", {}).get("rows", [])


def main():
    src = find_run(sys.argv[1] if len(sys.argv) > 1 else None)
    wd = Path(tempfile.mkdtemp(prefix="lens_kaveri_")) / src.name
    shutil.copytree(src, wd, ignore=shutil.ignore_patterns("lens.json", "inputs"))
    result = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    df = pd.read_pickle(wd / "clean.pkl")
    model = llm.get_llm()[0]
    if model is None:
        sys.exit("No AI model is configured (" + llm.get_llm()[1] + ").")
    print(f"Run {src.name}: {len(df):,} rows. Model: {llm.get_llm()[1]}\n")
    amount = df["amount"]
    q_ = lambda c: c.sum()
    checks = []

    def ask(question, verify, label=""):
        t = chat.ask(wd, result, question=question, tab="Report", llm=model, allow_ai=True)
        a = t["answer"]
        ok, expect = (False, "(no table)")
        if a["status"] == "answered":
            try:
                ok, expect = verify(t)
            except Exception as e:                         # a verifier that cannot find its figure is a mismatch, said plainly
                ok, expect = False, f"verifier error: {type(e).__name__}: {e}"
        print(f"Q{len(checks) + 1}{label}: {question}")
        print(f"   status: {a['status']}   AI calls: {a.get('ai_calls')}   chart: {(a.get('chart') or {}).get('type')}")
        print(f"   answer: {a['text']}")
        print(f"   query:  {json.dumps(a.get('query'), default=str)}")
        print(f"   pandas: {expect}")
        print(f"   MATCH:  {'YES' if ok else 'NO'}\n")
        checks.append(ok)
        return t

    def top_city(t):
        s = df.groupby("city")["amount"].sum().sort_values(ascending=False)
        r = rows(t)[0]
        return r[0] == s.index[0] and abs(r[1] - s.iloc[0]) < 0.01, f"{s.index[0]} = {s.iloc[0]:,.2f}"

    ask("Which city has the highest total revenue?", top_city)

    def top_city_gold(t):
        g = df[df.tier == "Gold"].groupby("city")["amount"].sum().sort_values(ascending=False)
        r = rows(t)[0]
        return r[0] == g.index[0] and abs(r[1] - g.iloc[0]) < 0.01, f"Gold only: {g.index[0]} = {g.iloc[0]:,.2f}"

    ask("And for Gold tier customers?", top_city_gold, " (follow-up)")

    def monthly(t):
        s = df.groupby(df.order_date.dt.to_period("M"))["amount"].sum()
        rr = rows(t)
        return len(rr) == len(s) and all(abs(r[1] - v) < 0.01 for r, v in zip(rr, s.values)), f"{len(s)} months, first {s.iloc[0]:,.2f}, last {s.iloc[-1]:,.2f}, peak {s.max():,.2f}"

    t3 = ask("How has revenue changed month by month?", monthly)

    def bars(t):
        s = df.groupby(df.order_date.dt.to_period("M"))["amount"].sum()
        rr = rows(t)
        return t["answer"]["chart"]["type"] == "bar" and len(rr) == len(s) and abs(rr[0][1] - s.iloc[0]) < 0.01, f"same {len(s)} months, drawn as bars (chart type: {t['answer']['chart']['type']})"

    ask("Show it as bars instead", bars, " (follow-up, chart control)")

    def margin(t):
        g = df.groupby("category").apply(lambda x: x.profit.sum() / x.amount.sum(), include_groups=False)
        rr = {r[0]: r[1] for r in rows(t)}
        return len(rr) > 0 and all(abs(rr[k] - g[k]) < 1e-9 for k in rr), "; ".join(f"{k} {v * 100:.1f}%" for k, v in g.sort_values(ascending=False).items())

    ask("What is the profit margin by category?", margin)

    def cmp_(t):
        m, d = df[df.city == "Mumbai"].amount.mean(), df[df.city == "Delhi"].amount.mean()
        rr = rows(t)
        got = {r[0]: r[1] for r in rr[:2]}
        return abs(got.get("Mumbai", -1) - m) < 0.01 and abs(got.get("Delhi", -1) - d) < 0.01, f"Mumbai {m:,.2f}, Delhi {d:,.2f}"

    ask("Compare Mumbai and Delhi on average order value", cmp_)

    def share(t):
        s = df.groupby("payment_mode")["amount"].sum()
        s = s / s.sum() * 100
        rr = {r[0]: r[2] for r in rows(t) if r[0] != "All others"}
        return t["answer"]["chart"]["type"] == "share" and all(abs(rr[k] - s[k]) < 1e-6 for k in rr), "; ".join(f"{k} {v:.1f}%" for k, v in s.sort_values(ascending=False).items()) + f" (chart type: {t['answer']['chart']['type']})"

    ask("What share of revenue comes from each payment mode? Show it as a share chart.", share, " (chart control)")

    def share_2024(t):
        d = df[df.order_date.dt.year == 2024]
        s = d.groupby("payment_mode")["amount"].sum()
        s = s / s.sum() * 100
        rr = {r[0]: r[2] for r in rows(t) if r[0] != "All others"}
        return len(rr) > 0 and all(abs(rr[k] - s[k]) < 1e-6 for k in rr), "2024 only: " + "; ".join(f"{k} {v:.1f}%" for k, v in s.sort_values(ascending=False).items())

    ask("Now only for 2024", share_2024, " (follow-up)")
    print(f"{sum(checks)} of {len(checks)} answers match pandas.")
    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
