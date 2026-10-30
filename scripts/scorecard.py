"""Scorecard: run the pipeline on a sample dataset (kaveri by default, or mall), check the report and findings against its truth.json,
print PASS/FAIL per check and append the result to scripts/scorecard_history.csv (with the dataset name).

    python scripts/scorecard.py              # run the pipeline on kaveri, then score it
    python scripts/scorecard.py --dataset mall --runs 2
    python scripts/scorecard.py --runs 3     # three pipeline runs one after another; per-check pass counts, median and minimum total
    python scripts/scorecard.py --run <id>   # score an existing run in backend/data/runs/ (no pipeline run)

Checks search the full report text (collapsed sections included) and the findings, so they measure facts, not layout.
Test tooling only: it imports the app but changes nothing in it. Questions are answered with their defaults."""
import argparse, csv, json, os, re, statistics, sys, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
HISTORY = Path(__file__).with_name("scorecard_history.csv")
SCOPE_Q = re.compile(r"main (number|metric|measure)|which (column|metric|measure)|segment|time ?frame|total or average|revenue or profit|amount or profit|what (should|do you want) .*(show|analy)", re.I)


MALL_GOAL = "Which brand and department earn the most revenue, and is it growing?"
DATASET = "kaveri"          # set by --dataset


def data_dir() -> Path:
    if DATASET == "mall":
        return ROOT / "sample_data" / "mall"
    for p in (ROOT / "sample_data" / "kaveri", ROOT / "sample_data" / "kaveri_test" / "kaveri_test"):
        if (p / "input.json").exists():
            return p
    sys.exit("Kaveri data folder with input.json not found under sample_data/")


BUSY_MINUTES = 15       # a run still marked running/queued this long after its last status change is a leftover of a killed process, not a live run


def busy_runs() -> list[str]:
    """Run ids another process is working on right now: queued or running, with a status written in the last BUSY_MINUTES."""
    runs = ROOT / "backend" / "data" / "runs"
    out = []
    for f in runs.glob("*/status.json"):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if st.get("status") in ("queued", "running") and time.time() - st.get("updated", st.get("created", 0)) < BUSY_MINUTES * 60:
            out.append(f.parent.name)
    return out


def run_pipeline(d: Path) -> str:
    from app import config  # noqa: F401  (loads .env)
    from app.runner import create_run, start_run, read_status, resume_with_answers, run_dir
    if DATASET == "mall":
        inp = {"goal": MALL_GOAL, "files": ["sales.csv"], "context": None}
    else:
        inp = json.loads((d / "input.json").read_text(encoding="utf-8"))
    rid = create_run(inp["goal"], [(f, (d / f).read_bytes()) for f in inp["files"]], context=inp["context"])
    print(f"run {rid} started...", flush=True)
    start_run(rid)

    def wait():
        while (st := read_status(rid)["status"]) not in ("done", "error", "needs_input"):
            time.sleep(3)
        return st
    st = wait()
    if st == "needs_input":
        qs = json.loads((run_dir(rid) / "questions.json").read_text(encoding="utf-8"))["questions"]
        resume_with_answers(rid, {"answers": {q["id"]: q.get("prefill") or q["default"] for q in qs}, "skip": False, "notes": ""})
        st = wait()
    if st != "done":
        raise RuntimeError(f"run {rid} ended with status {st}: {read_status(rid)}")
    return rid


def nums(text: str):
    """All numbers in text as (value, has_percent); K/M/B suffixes expanded, a leading - or minus sign kept."""
    out = []
    for m in re.finditer(r"([-+−]?)\$?₹?\s?(\d[\d,]*\.?\d*)\s?([KMB])?(%)?", text):
        try:
            v = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        v *= {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(3), 1)
        if m.group(1) in ("-", "−"):
            v = -v
        out.append((v, bool(m.group(4))))
    return out


def has_num(text: str, target: float, tol: float, pct=None) -> bool:
    return any(abs(v - target) <= tol and (pct is None or p == pct) for v, p in nums(text))


def lines_with(text: str, word: str):
    return [l for l in text.splitlines() if re.search(word, l, re.I)]


def full_report(res: dict) -> str:
    """The whole report text, collapsed sections included (the <details> and <summary> tags removed, their text kept): checks measure facts, not where the report puts them."""
    return re.sub(r"</?(?:details|summary)[^>]*>|<a id=[^>]*></a>", "", res.get("report_md") or "")


def score(rid: str):
    return score_mall(rid) if DATASET == "mall" else score_kaveri(rid)


def score_mall(rid: str):
    wd = ROOT / "backend" / "data" / "runs" / rid
    r = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    t = json.loads((data_dir() / "truth.json").read_text(encoding="utf-8"))
    rep = full_report(r)
    findings = r.get("findings") or []
    alltext = rep + chr(10) + chr(10).join(f"{f.get('title', '')}. {f.get('text', '')}" for f in findings)
    rows = []

    def add(n, name, ok, detail):
        rows.append((n, name, bool(ok), detail))
    rev = t["total_gross_revenue"]
    got = [v for v, _ in nums(alltext) if abs(v - rev) <= rev * 0.015]
    add(1, "Revenue total (within 1.5%: blank Qty)", got, f"truth {rev:,.0f}; found {got[0]:,.0f}" if got else "not found")
    add(2, "Top store Phoenix Mall", re.search(r"Phoenix Mall is highest", alltext), "")
    ls = [l for l in alltext.splitlines() if "Lakeside Outlet" in l and re.search(r"-\d+(\.\d+)?%", l)]
    add(3, "Shrinking store Lakeside Outlet", ls, ls[0][:90] if ls else "no decline line")
    ur = [l for l in alltext.splitlines() if "Urban Weave" in l and re.search(r"36\.5%|37%", l)]
    add(4, "High-return brand Urban Weave 36.5%", ur, ur[0][:90] if ur else "")
    add(5, "Festive peak Oct/Nov, weak Jun", re.search(r"highest Oct", alltext) and re.search(r"lowest Jun", alltext), "month pattern: highest Oct, lowest Jun" if re.search(r"highest Oct", alltext) else "")
    md = [l for l in alltext.splitlines() if "Menswear" in l and re.search(r"49(\.\d)?%", l)]
    add(6, "Dominant dept Menswear ~49%", md, md[0][:90] if md else "not stated with its share")
    dec = [l for l in alltext.splitlines() if re.search(r"Pay Mode|Size", l) and re.search(r"driver|drives|strongest", l, re.I)]
    add(7, "Decoys not called drivers", not dec, "; ".join(dec)[:90])
    gap = "Margin: not computable, no cost column"
    bad = re.findall(r"(?i)margin|\bprofit\w*|markup", chr(10).join(l for l in rep.splitlines() if gap not in l))
    add(8, "No invented margin/profit", not bad, f"found {set(bad)}" if bad else "only the data-gap line")
    add(9, "Gap line says margin not computable", gap in rep, "")
    bn = [f["id"] for f in findings if "Bill No" in json.dumps(f.get("params", {}))]
    add(10, "Bill No not a measure", not bn, str(bn))
    qs = json.loads((wd / "questions.json").read_text(encoding="utf-8")).get("questions", []) if (wd / "questions.json").exists() else []
    asked = [q for q in qs if not q.get("auto")]
    add(11, "<=3 questions, relevant", len(asked) <= 3 and {q["kind"] for q in asked} >= {"flag_rows", "blank_values"}, ", ".join(q["kind"] for q in asked))
    log = " ".join(s["detail"] for s in r.get("cleaning_log") or [])
    add(12, "All five mess items handled", all(k in log for k in ("Rs", "day-first", "raymond", "30 exact duplicate", "revenue' = Qty")) or all(k in log for k in ("currency", "day-first", "raymond", "30 exact duplicate")), "")
    return rows


def score_kaveri(rid: str):
    wd = ROOT / "backend" / "data" / "runs" / rid
    res = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    truth = json.loads((data_dir() / "truth.json").read_text(encoding="utf-8"))
    report = full_report(res)
    findings = res.get("findings") or []
    qs = json.loads((wd / "questions.json").read_text(encoding="utf-8")).get("questions", []) if (wd / "questions.json").exists() else []
    ftext = "\n".join(f"{f.get('title', '')}. {f.get('text', '')}" for f in findings)
    alltext = report + "\n" + ftext
    rows = []

    def add(n, name, ok, detail):
        rows.append((n, name, bool(ok), detail))

    # 1 revenue
    rev = truth["total_revenue"]
    found = [v for v, _ in nums(alltext) if abs(v - rev) <= rev * 0.001]
    add(1, "Revenue total", found, f"truth {rev:,.0f}; " + (f"found {found[0]:,.0f}" if found else "not found within 0.1%"))

    # 2 profit + margin
    pr, mg = truth["total_profit"], truth["overall_margin_pct"]
    p_ok = any(abs(v - pr) <= pr * 0.001 for v, _ in nums(alltext))
    m_ok = any(abs(v - mg) <= 0.3 and p for v, p in nums(alltext)) or any(abs(v - mg / 100) <= 0.003 for v, _ in nums(alltext) if 0 < v < 1)
    add(2, "Profit total and margin", p_ok and m_ok, f"truth {pr:,.0f} / {mg}%; profit total {'found' if p_ok else 'MISSING'}, margin {'found' if m_ok else 'MISSING'}")

    # 3 top tier + share
    tiers = truth["by_tier"]
    top = max(tiers, key=lambda t: tiers[t]["revenue"])
    share = tiers[top]["revenue"] / sum(t["revenue"] for t in tiers.values()) * 100
    sh = [l for l in lines_with(alltext, top) if has_num(l, share, 0.6, True)]
    add(3, f"{top} top tier, ~{share:.1f}% share", sh, "share sentence found" if sh else f"no sentence naming {top} with a {share:.1f}% share")

    # 4 tier growth
    g = truth["tier_growth_2024_vs_2023_pct"]
    gd, gok = [], True
    for t, v in g.items():
        hit = [l for l in lines_with(alltext, rf"\b{t}\b") if has_num(l, v, 6, None)]
        gok &= bool(hit)
        gd.append(f"{t} {v:+.0f}% {'ok' if hit else 'MISSING'}")
    add(4, "Tier growth 2023->2024", gok, "; ".join(gd))

    # 5 tier margin does not reliably differ (anywhere in the full report or the findings)
    s5 = re.search(r"(margin|ratio)[^.\n]{0,80}(not|no)[^.\n]{0,40}(reliabl|significan|differ)|(not|no)[^.\n]{0,40}(reliabl|significan)[^.\n]{0,80}(margin|ratio)", report, re.I) \
        or re.search(r"(margin|ratio).*does not differ reliably", ftext, re.I)
    add(5, "Tier margin not reliably different", s5, "stated in the report" if s5 else "not stated")

    # 6 Electronics loss-making
    el = truth["by_category"]["Electronics"]["margin_pct"]
    e6 = [l for l in lines_with(alltext, "electronics") if re.search(r"loss|losing|negative|unprofitable", l, re.I) and has_num(l, el, 0.6, True)]
    add(6, f"Electronics loss-making (~{el}%)", e6, "found" if e6 else "no line naming Electronics as loss-making with its margin")

    # 7 discount vs margin
    r7 = truth["corr_discount_vs_margin"]
    f7 = [f for f in findings for k, v in (f.get("stats", {}).get("top_pairs") or {}).items()
          if "discount" in k and re.search("ratio|margin", k) and abs(v - r7) <= 0.03]
    add(7, f"Discount vs margin (r~{r7})", f7, f"finding {f7[0]['id']}" if f7 else "no discount/margin correlation near truth")

    # 8 seasonality
    idx = truth["revenue_by_month_of_year_index"]
    paras = [p for p in re.split(r"\n\s*\n", alltext) if re.search(r"season|month of the year|month-of-year|calendar month", p, re.I)]
    hi = [p for p in paras if re.search(r"Oct", p) and re.search(r"Aug", p)]
    add(8, "Seasonality: Oct high, Aug low", hi, "found" if hi else f"no month-of-year seasonality naming Oct and Aug (truth: month {max(idx, key=idx.get)} high, {min(idx, key=idx.get)} low)")

    # 9 questions
    asked_qs = [q for q in qs if not q.get("auto")]        # questions the data or the goal settled ("auto") are recorded in the file but never put to the user
    scope = [q["text"] for q in asked_qs if SCOPE_Q.search(q["text"])]
    add(9, "<=3 questions, none scope", len(asked_qs) <= 3 and not scope, f"{len(asked_qs)} asked" + (f" (+{len(qs) - len(asked_qs)} settled by data)" if len(qs) > len(asked_qs) else "") + (f"; scope: {scope}" if scope else ""))

    # 10 placeholder 999
    asked = [q["text"] for q in qs if re.search(r"999|delivery_days|placeholder", q["text"], re.I)]
    logged = [l for l in lines_with(report, r"999|delivery_days") if re.search(r"missing|placeholder|ignored|not a real|treated", l, re.I) and "| delivery_days |" not in l]
    add(10, "999 delivery_days handled", asked or logged, "asked" if asked else ("logged" if logged else "not asked and not logged (only appears as a raw max)"))

    # 11 pincode
    bad = [f["id"] for f in findings if "pincode" in json.dumps(f.get("params", {})) + str(f.get("title", "")) + json.dumps(f.get("stats", {}).get("columns", ""), default=str) + json.dumps(f.get("stats", {}).get("top_pairs", ""))]
    add(11, "Pincode not a measure", not bad, f"used in findings {bad}" if bad else "not used in any finding")

    # 12 ratios are sum/sum
    ratios = [d["name"] for d in (res.get("derived_metrics") or {}).get("created", []) if d.get("kind") == "ratio_of_sums"]
    probs = []
    for f in findings:
        tgt = f.get("params", {}).get("target")
        if tgt not in ratios:
            continue
        st = f.get("stats", {})
        if st.get("agg") == "mean":
            probs.append(f"{f['id']} trend of {tgt} is a mean of row ratios")
        if f.get("params", {}).get("group") == "tier" and st.get("values"):
            for t, v in st["values"].items():
                exp = tiers[t]["profit"] / tiers[t]["revenue"]
                if abs(v - exp) > abs(exp) * 0.005:
                    probs.append(f"{f['id']} {t} {v:.4f} vs sum/sum {exp:.4f}")
    add(12, "Ratios are sum/sum", ratios and not probs, "; ".join(probs[:4]) if probs else (f"{len(ratios)} ratio(s), all consistent" if ratios else "no ratio exists (profit margin was not created)"))
    return rows


def ratio_path(rid: str) -> str:
    """Which route produced the ratio KPI in this run (llm, llm-retry or rules), for the run header."""
    try:
        b = json.loads((ROOT / "backend" / "data" / "runs" / rid / "brief.json").read_text(encoding="utf-8"))["brief"]
        return ", ".join(f"{k}: {m['path']}" for k, m in (b.get("ratios") or {}).items()) or "none"
    except (OSError, ValueError, KeyError):
        return "unknown"


def table(rows) -> None:
    print(f"{'#':>2}  {'Result':<6}  {'Check':<40}  Detail")
    for n, name, ok, detail in rows:
        print(f"{n:>2}  {'PASS' if ok else 'FAIL':<6}  {name[:40]:<40}  {' '.join(str(detail).split())[:90]}")


def save(rid: str, rows) -> None:
    if HISTORY.exists():
        old = list(csv.reader(HISTORY.open(newline="", encoding="utf-8")))
        if old and "dataset" not in old[0]:        # history written before --dataset existed: every row was kaveri
            with HISTORY.open("w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows([old[0] + ["dataset"]] + [r + ["kaveri"] for r in old[1:]])
    new = not HISTORY.exists()
    with HISTORY.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["timestamp", "run_id", "score", "total"] + [f"c{r[0]}" for r in rows] + ["dataset"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), rid, sum(r[2] for r in rows), len(rows)] + [int(r[2]) for r in rows] + [DATASET])


def ai_usage_table(rid: str) -> None:
    """Per-stage AI usage of a run, from its ai_usage.json: calls, saved answers reused, tokens (estimated when the provider gives none) and time."""
    f = run_dir_of(rid) / "ai_usage.json"
    if not f.exists():
        print("AI usage: no ai_usage.json in this run\n")
        return
    from app import llm as llm_mod
    calls = json.loads(f.read_text(encoding="utf-8")).get("calls", [])
    rows = llm_mod.by_stage(calls)
    print(f"{'Stage':<38} {'Calls':>5} {'Saved':>5} {'Failed':>6} {'In tokens':>10} {'Out tokens':>10} {'Seconds':>8}")
    for r in rows:
        mark = "~" if r["estimated"] else " "
        print(f"{r['stage'][:38]:<38} {r['calls']:>5} {r['cached']:>5} {r['failed']:>6} {mark}{r['input_tokens']:>9,} {mark}{r['output_tokens']:>9,} {r['seconds']:>8.1f}")
    t = llm_mod.totals(calls)
    print(f"{'Total':<38} {t['calls']:>5} {t['cached']:>5} {sum(r['failed'] for r in rows):>6} {t['input_tokens']:>10,} {t['output_tokens']:>10,} {t['seconds']:>8.1f}")
    print("~ = estimated (the provider gave no token count; about 4 characters a token)\n")


def run_dir_of(rid: str) -> Path:
    from app.runner import run_dir
    return run_dir(rid)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="score an existing run id instead of running the pipeline")
    ap.add_argument("--runs", type=int, default=1, help="number of pipeline runs, one after another")
    ap.add_argument("--dataset", choices=("kaveri", "mall"), default="kaveri")
    ap.add_argument("--no-cache", action="store_true", help="do not reuse saved AI results (sets AI_CACHE=off)")
    a = ap.parse_args()
    if a.no_cache:
        os.environ["AI_CACHE"] = "off"
    global DATASET
    DATASET = a.dataset
    if a.run:
        rows = score(a.run)
        print(f"\n{DATASET} scorecard, run {a.run}\n")
        table(rows)
        ai_usage_table(a.run)
        print(f"\nTotal: {sum(r[2] for r in rows)}/{len(rows)}")
        return
    if busy := busy_runs():
        sys.exit(f"Refusing to start: another pipeline run is in progress ({', '.join(busy)}). Wait for it to finish, then run this again.")
    d = data_dir()
    results = []        # one list of rows per pipeline run that finished
    for i in range(1, a.runs + 1):
        from app import llm as llm_mod
        sent_before = llm_mod.SENT["calls"]
        try:
            rid = run_pipeline(d)
        except RuntimeError as e:
            print(f"\nrun {i}/{a.runs} FAILED: {e}", flush=True)
            continue
        rows = score(rid)
        results.append(rows)
        print(f"\n{DATASET} scorecard, run {i}/{a.runs} ({rid}); ratio KPI path: {ratio_path(rid)}")
        rep = run_dir_of(rid) / "report.md"
        reuse = re.search(r"Reused saved AI result: [^\n]*", rep.read_text(encoding="utf-8", errors="replace")) if rep.exists() else None
        print(f"AI requests sent in this run: {llm_mod.SENT['calls'] - sent_before}; cache {'off' if a.no_cache else 'on'}; {reuse.group(0) if reuse else 'no saved AI result reused'}\n")
        table(rows)
        ai_usage_table(rid)
        print(f"\nTotal: {sum(r[2] for r in rows)}/{len(rows)}", flush=True)
        save(rid, rows)
    if not results:
        sys.exit("No run finished.")
    if a.runs > 1:
        n = len(results)
        print(f"\nPer-check passes across {n} finished run(s) of {a.runs}\n")
        print(f"{'#':>2}  {'Passed':<7}  Check")
        for k, (num, name, _, _) in enumerate(results[0]):
            print(f"{num:>2}  {str(sum(r[k][2] for r in results)) + '/' + str(n):<7}  {name[:60]}")
        totals = [sum(r[2] for r in rows) for rows in results]
        print(f"\nTotals per run: {totals}\nMedian: {statistics.median(totals)}/{len(results[0])}   Minimum: {min(totals)}/{len(results[0])}")


if __name__ == "__main__":
    main()
