"""Real-AI check of the Lens fix pack on the Kaveri run: eight questions, in one conversation. For each: the question, the answer, and whether the figures match what pandas gives
directly on the cleaned data (computed here with plain pandas/numpy, not by Lens).

    python scripts/lens_fixpack_check.py [run_id]       # default: the newest finished Kaveri run in backend/data/runs

Needs a configured AI (GROQ_API_KEY in .env). Works on a copy of the run, so the run itself is not changed. Stops at once, saying so, if the AI's daily token limit is hit."""
import json, os, re, shutil, sys, tempfile, time
from pathlib import Path

os.environ["AI_CACHE"] = "off"       # every question really goes to the model
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import config, llm  # noqa: E402,F401  (loads .env)
from app.lens import chat, engine, reports  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from lens_kaveri_check import find_run  # noqa: E402

checks = []


def per_group(df, q, g):
    m, agg = q.get("measure"), q.get("agg")
    gb = df.groupby(g)
    return (gb[m].mean() if agg == "average" else gb[m].sum() if agg == "sum" else gb.size())


def main():
    src = find_run(sys.argv[1] if len(sys.argv) > 1 else None)
    wd = Path(tempfile.mkdtemp(prefix="lens_fixpack_")) / src.name
    shutil.copytree(src, wd, ignore=shutil.ignore_patterns("lens.json", "inputs"))
    result = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    df = pd.read_pickle(wd / "clean.pkl")
    ctx = chat.load_data(wd, result)[1]
    model = llm.get_llm()[0]
    if model is None:
        sys.exit("No AI model is configured (" + llm.get_llm()[1] + ").")
    print(f"Run {src.name}: {len(df):,} rows. Model: {llm.get_llm()[1]}\n")

    def ask(question, verify):
        t = chat.ask(wd, result, question=question, tab="Report", llm=model, allow_ai=True)
        a = t["answer"]
        if "daily limit" in a.get("text", "") or time.time() < chat._DAILY["until"]:
            print(f"Q{len(checks) + 1}: {question}\n   STOPPED: the AI's daily token limit was reached. {len(checks)} question(s) were answered before that. Nothing more was run.")
            sys.exit(3)
        try:
            ok, expect = verify(a)
        except Exception as e:                         # a verifier that cannot find its figure is a mismatch, said plainly
            ok, expect = False, f"verifier error: {type(e).__name__}: {e}"
        print(f"Q{len(checks) + 1}: {question}")
        print(f"   status: {a['status']}   AI calls: {a.get('ai_calls')}   kind: {a.get('kind', 'data')}   chart: {(a.get('chart') or {}).get('type')}")
        print(f"   answer: {a['text']}")
        for ln in a.get("lines") or []:
            print(f"      - {ln}")
        print(f"   query:  {json.dumps(a.get('query'), default=str)}")
        print(f"   pandas: {expect}")
        print(f"   MATCH:  {'YES' if ok else 'NO'}\n")
        checks.append(ok)
        return a

    # 1. split-by
    def split_by(a):
        q = a["query"]
        g, s = q["group_by"][0], q["split_by"][0]
        pv = df.groupby([g, s])[q["measure"]].mean()
        overall = df.groupby(g)[q["measure"]].mean()
        cols = a["table"]["columns"][1:]
        cells_ok = all((v is None and pd.isna(pv.get((r[0], c), np.nan))) or (v is not None and abs(v - pv.get((r[0], c), np.nan)) < 1e-6) for r in a["table"]["rows"] for c, v in zip(cols, r[1:]))
        largest = next(f["value"] for f in a["figures"] if f["label"] == "largest combination")
        lead = next(f["value"] for f in a["figures"] if f["label"].lower().startswith("average"))
        R = engine.run(q, df, ctx)
        ok = (q["op"] == "aggregate" and q["agg"] == "average" and cells_ok and abs(largest - pv.max()) < 1e-6 and largest >= overall.max() - 1e-9 and abs(lead - overall.max()) < 1e-6 and engine.drawn_vs_table(R) == [])
        return ok, f"overall {overall.idxmax()} = {overall.max():,.2f}; largest combination {pv.idxmax()[0]} / {pv.idxmax()[1]} = {pv.max():,.2f} (never below its group's overall); every table cell and the drawn values match: {cells_ok}"
    ask("average Profit by Category, split by Sub Category", split_by)

    # 2. which categories lose money
    def lose(a):
        q = a["query"]
        v = per_group(df, q, q["group_by"][0])
        neg = list(v[v < 0].index)
        rows = [r[0] for r in a["table"]["rows"]]
        named = all(n in a["text"] for n in neg) and ("negative" in a["text"] or "lose money" in a["text"])
        return named and set(neg) <= set(rows) and (not q.get("only_negative") or rows == neg), f"{q.get('agg')} {q.get('measure')} below zero: {neg} ({', '.join(f'{k} {x:,.0f}' for k, x in v.items())})"
    ask("which categories lose money", lose)

    # 3. total profit over time, then "not plotted properly"
    def trend(a):
        s = df.groupby(df.order_date.dt.to_period("M"))["profit"].sum()
        rr = a["table"]["rows"]
        return a["query"]["op"] == "trend" and a["query"]["measure"] == "profit" and len(rr) == len(s) and all(abs(r[1] - v) < 0.01 for r, v in zip(rr, s.values)), f"{len(s)} months, first {s.iloc[0]:,.2f}, last {s.iloc[-1]:,.2f}"
    t1 = ask("show total profit over time", trend)

    def recheck(a):
        s = df.groupby(df.order_date.dt.to_period("M"))["profit"].sum()
        R = engine.run(a["query"], df, ctx)
        return a.get("kind") == "recheck" and a["query"] == t1["query"] and engine.drawn_vs_table(R) == [] and "matches the computed table" in a["text"] and all(abs(r[1] - v) < 0.01 for r, v in zip(a["table"]["rows"], s.values)), "same chart as the question above; drawn values equal the pandas monthly totals"
    ask("the graph is not plotted properly", recheck)

    # 4. unusual changes (independently calculated)
    def unusual(a):
        q = a["query"]
        m = q.get("measure")
        d = df.assign(_p=df.order_date.dt.to_period("M"))
        g = d.groupby("_p")[m].sum() if q["agg"] == "sum" else d.groupby("_p")[m].mean()
        n = d.groupby("_p").size()
        if df.order_date.min() > g.index[0].start_time:
            g, n = g.iloc[1:], n.iloc[1:]
        if df.order_date.max() < g.index[-1].end_time.normalize():
            g, n = g.iloc[:-1], n.iloc[:-1]
        pct = g.pct_change() * 100
        med = pct.dropna().median()
        mad = 1.4826 * (pct.dropna() - med).abs().median()
        big = (pct.abs() >= 40) | (((pct - med).abs() > 3 * mad) & (pct.abs() >= 10))
        want = [str(p) for p in g.index[big & (n >= 5) & (n.shift() >= 5)]]
        got = [r[0] for r in a["table"]["rows"] if r[4]]
        return q["op"] == "unusual_changes" and got == want and all(w in a["text"] for w in want[:8]), f"flagged months by an independent calculation: {want or 'none'}; Lens flagged: {got or 'none'}"
    ask("any sudden drop or rise?", unusual)

    # 5. premise
    def premise(a):
        q = a.get("query") or {}
        m = {"measure": q.get("measure"), "agg": q.get("agg") or "sum"}
        v = per_group(df, {**m, "agg": m["agg"] if m["agg"] in ("sum", "average") else "sum"}, "category")
        lead = v.idxmax()
        txt = a["text"]
        right = txt.startswith("Correction first:") and f"{lead} is," in txt and re.search(r"Clothing is (?:second|third|fourth|last)", txt) is not None
        return right, f"by {m['agg']} {m['measure']}: {lead} is top at {v.max():,.2f}; Clothing is {v['Clothing']:,.2f}, so “it should be Clothing” is wrong and the correction must come first"
    ask("why is Furniture the top, it should be Clothing", premise)

    # 6. summarise
    def summary(a):
        known = __import__("app.lens.records", fromlist=["x"]).known_facts(result)
        nums_ok = all(any(abs(float(n.replace(",", "")) - float(x)) < 0.051 * max(1, abs(float(n.replace(",", "")))) or abs(float(n.replace(",", "")) - float(x)) < 0.051 for f in known.values() for x in f["numbers"])
                      for ln in a["lines"] for n in re.findall(r"\d[\d,]*\.?\d*", re.sub(r"\b(?:19|20)\d\d\b", " ", ln)) if not n.endswith(",") and re.sub(r"[^\d]", "", n))
        return a["kind"] == "summary" and 1 <= len(a["lines"]) <= 5 and all(reports.words_in(x) <= 20 and not reports.repeats_a_label(x) and re.search(r"\d", x) for x in a["lines"]) and nums_ok, \
            f"{len(a['lines'])} lines, longest {max(reports.words_in(x) for x in a['lines'])} words; every figure is in the report's fact list: {nums_ok}; wording by: {a.get('source_of_words')}"
    ask("summarise the report", summary)

    # 7. shorten the ideas tab
    def view(a):
        return a.get("kind") == "view" and a.get("ai_calls") == 0 and not a.get("lines") and all(w in a["text"] for w in ("Use as my short version", "Pin", "Change the chart")), "a view change: not summarised, no AI used, the three things that work now are listed"
    ask("shorten the ideas tab", view)

    # 8. best strategy and why
    def strategy(a):
        st = [s for s in result.get("strategies") or [] if s.get("kind", "data") == "data"]
        txt = " ".join(a["lines"])
        first = a["lines"][0] if a["lines"] else ""
        top = next((s for s in st if first.startswith(s["title"].rstrip("."))), None)
        runner = re.search(r"The runner-up is “([^”]+)” \(impact (\w+), confidence (\w+), effort (\w+)\)", txt)
        run_rec = next((s for s in st if runner and s["title"].rstrip(".") == runner.group(1)), None)
        ok = bool(top and "Why this one:" in a["lines"] and runner and run_rec and (run_rec["impact"], run_rec["confidence"], run_rec["effort"]) == runner.groups()[1:]
                  and f"impact is {top['impact']}, the confidence {top['confidence']} and the effort {top['effort']}" in txt and "ranked lower because" in txt)
        return ok, "ratings and the runner-up quoted from the report's own strategy records: " + (f"{top['title'][:60]} ({top['impact']}/{top['confidence']}/{top['effort']}) vs {run_rec['title'][:50] if run_rec else '?'}" if top else "no match")
    a8 = ask("best strategy and why", strategy)
    chip = next((c for c in a8.get("chips", []) if c["text"] == "Why did you choose it?"), None)
    print(f"   (follow-up chip offered: {chip['text'] if chip else 'none'})\n")

    print(f"{sum(checks)} of {len(checks)} answers match pandas.")
    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
