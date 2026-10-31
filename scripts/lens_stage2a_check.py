"""Real-AI check of Lens Stage 2A on the Kaveri run: nine questions. For each: the question, the answer, and whether its figures match pandas (or, for answers quoted from the
report, the report's own saved records and fact list).

    python scripts/lens_stage2a_check.py [run_id]       # default: the newest finished Kaveri run with an AI understanding record in backend/data/runs

Needs a configured AI (GROQ_API_KEY in .env). Works on a copy of the run, so the run itself is not changed. If the AI's daily token limit is hit the script stops and says so; it never retries."""
import json, os, re, shutil, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

os.environ["AI_CACHE"] = "off"       # every question really goes to the model
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import config, llm  # noqa: E402,F401  (loads .env)
from app.lens import chat, records  # noqa: E402
import pandas as pd  # noqa: E402

RUNS = ROOT / "backend" / "data" / "runs"
CAUSAL = re.compile(r"\b(because|due to|caused?|driven by|as a result of|the reason is|thanks to)\b", re.I)


def find_run(arg):
    if arg:
        return RUNS / arg
    best = None
    for d in RUNS.glob("*/result.json"):
        r = json.loads(d.read_text(encoding="utf-8"))
        if "Which customer tier" in (r.get("goal") or "") and (r.get("understanding") or {}).get("source") == "llm" and (d.parent / "clean.pkl").exists() and r.get("report_view"):
            if best is None or d.stat().st_mtime > best.stat().st_mtime:
                best = d
    if best is None:
        sys.exit("No finished Kaveri run with an AI understanding record found; run scripts/scorecard.py first.")
    return best.parent


class DailyLimit(Exception):
    pass


def nums(text):
    t = re.sub(r"\[[a-z]\d+\]", " ", text)
    t = re.sub(r"\b\d{4}-(?:Q\d|\d{2}(?:-\d{2})?)\b|\b(?:19|20)\d\d\b", " ", t)
    return [float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*\.?\d*", t) if m.replace(",", "").strip(".")]


def in_facts(text, known):
    """Every figure written is a figure of some fact in the report's checked list (the same rounding rules the report's own checks use)."""
    from app.insights import unverified_numbers
    allowed = [x for f in known.values() for x in f["numbers"]]
    return not unverified_numbers(re.sub(r"\[[a-z]\d+\]", "", text), allowed)


def main():
    src = find_run(sys.argv[1] if len(sys.argv) > 1 else None)
    wd = Path(tempfile.mkdtemp(prefix="lens_s2a_")) / src.name
    shutil.copytree(src, wd, ignore=shutil.ignore_patterns("lens.json", "inputs"))
    result = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    df = pd.read_pickle(wd / "clean.pkl")
    model = llm.get_llm()[0]
    if model is None:
        sys.exit("No AI model is configured (" + llm.get_llm()[1] + ").")
    known = records.known_facts(result)
    print(f"Run {src.name}: {len(df):,} rows. Model: {llm.get_llm()[1]}. Report trust: {records.trust_of(result)}\n")
    checks, calls = [], []

    def ask(question, verify, tab="Report"):
        t = chat.ask(wd, result, question=question, tab=tab, llm=model, allow_ai=True)
        a = t["answer"]
        if a["status"] == "unavailable" and "daily limit" in a["text"].lower():
            print(f"Q{len(checks) + 1}: {question}\n   STOPPED: the AI's daily token limit is hit ({a['text']}). Not retrying.")
            raise DailyLimit
        try:
            ok, expect = verify(a) if a["status"] == "answered" else (False, f"(status {a['status']})")
        except Exception as e:
            ok, expect = False, f"verifier error: {type(e).__name__}: {e}"
        print(f"Q{len(checks) + 1}: {question}")
        print(f"   status: {a['status']}   kind: {a.get('kind', 'data')}   AI calls: {a.get('ai_calls')}   label: {a.get('label', '')}")
        print(f"   answer: {a['text']}")
        for ln in a.get("lines") or []:
            print(f"      - {ln}")
        for ln in a.get("actions") or []:
            print(f"      * action: {ln}")
        print(f"   chips:  {[c['text'] for c in a.get('chips') or []]}")
        print(f"   check:  {expect}")
        print(f"   MATCH:  {'YES' if ok else 'NO'}\n")
        checks.append(ok)
        calls.append(a.get("ai_calls") or 0)
        return a

    s = df.groupby("category")["amount"].sum().sort_values(ascending=False)
    top, second = s.index[0], s.index[1]

    def premise(a):
        t = a["text"]
        ok = (t.startswith(f"Correction first: {second} is not the top category") and f"{s.iloc[0]:,.0f}" in t.replace("₹", "") and f"{s.iloc[1]:,.0f}" in t.replace("₹", "")
              and "The data alone can't show why" in t and not CAUSAL.search(t))
        return ok, f"pandas: {top} = {s.iloc[0]:,.2f}, {second} = {s.iloc[1]:,.2f}; the correction comes first, no cause is claimed"

    ask(f"Why is {second} the top category by revenue? It should be {top}.", premise)

    def why_true(a):
        share = s.iloc[0] / s.sum() * 100
        t = a["text"]
        return f"{share:.1f}%" in t and not CAUSAL.search(t) and "Correction" not in t and t.startswith("The data alone can't show why"), f"pandas: {top} is {share:.1f}% of total amount ({s.iloc[0]:,.2f} of {s.sum():,.2f})"

    ask(f"Why is {top} the top category?", why_true)

    def why_change(a):
        y = df.groupby(df.order_date.dt.year)["amount"].sum()
        t = a["text"]
        ok = not CAUSAL.search(t) and t.startswith("The data alone can't show why") and any(f"{v:,.0f}" in t for v in y.values)
        return ok, "pandas yearly totals: " + ", ".join(f"{k}: {v:,.0f}" for k, v in y.items())

    ask("Why did revenue change between 2023 and 2024?", why_change)

    cl = result.get("cleaning_log") or []

    def cleaned(a):
        want = [f"{c['step']}: " for c in cl[:3]]
        ok = a["kind"] == "app" and all(any(w in ln for ln in a["lines"]) for w in want) and len(cl) > 0 and str(len(cl)) in a["text"]
        return ok, f"saved cleaning log has {len(cl)} steps; first: {cl[0]['step']} ({cl[0].get('count')} rows)" if cl else "no cleaning log saved"

    ask("What did you clean?", cleaned, tab="What we cleaned")

    lvl = records.trust_of(result)

    def trust(a):
        why = [c.get("trust_why") for c in (result["report_view"]["page"].get("cards") or []) if c.get("trust_why")]
        ok = a["kind"] == "app" and f"overall trust is {lvl}" in a["text"] and all(any(w in ln for ln in a["lines"]) for w in why[:2])
        return ok, f"saved trust: {lvl}; saved reasons: {why[:2]}"

    ask(f"Why is trust {lvl}?", trust)

    def summary(limit):
        def v(a):
            ok = a["kind"] == "summary" and 1 <= len(a["lines"]) <= limit and all(in_facts(ln, known) for ln in a["lines"]) and len(a["actions"]) <= 3
            ok = ok and a["actions"] == [x["text"] for x in records.action_items(result)[:3]]
            return ok, f"{len(a['lines'])} lines (max {limit}); every figure is in the report's fact list: {all(in_facts(ln, known) for ln in a['lines'])}; actions = the report's top {len(a['actions'])}"
        return v

    ask("Summarise this report", summary(5))
    ask("Shorten it, I only have a minute", summary(3))

    def priority(word, pandas_note):
        def v(a):
            ok = a["kind"] == "priority" and len(a["lines"]) <= 3 and all(in_facts(ln, known) for ln in a["lines"]) and (len(a["lines"]) > 0 or "Nothing in this report" in a["text"])
            return ok, f"top {len(a['lines'])}; figures in the report's fact list: {all(in_facts(ln, known) for ln in a['lines'])}; {pandas_note}"
        return v

    p1 = ask("I care about discounts", priority("discount", f"pandas: mean discount_pct {df.discount_pct.mean():.2f}"))
    p2 = ask("I care about profit", priority("profit", f"pandas: total profit {df.profit.sum():,.0f}"))
    differ = p1.get("lines") != p2.get("lines")
    print(f"The two priorities give different rankings: {'YES' if differ else 'NO'}")
    checks.append(differ)
    print(f"\nTotal AI calls: {sum(calls)} (max 2 per question: {'OK' if max(calls) <= 2 else 'EXCEEDED'}).  Matches: {sum(checks)}/{len(checks)}")


if __name__ == "__main__":
    try:
        main()
    except DailyLimit:
        sys.exit(2)
