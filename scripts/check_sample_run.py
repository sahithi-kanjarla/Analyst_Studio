"""Check a finished run of the demo sample against sample_data/demo/expected.json.

    python scripts/check_sample_run.py [result.json] [expected.json]

Default result: sample_data/demo/sales_run/result.json. Reads only the run's saved files; it makes no
AI call. The Dashboard tab is rebuilt with the app's own dashboard builder when the run folder has
clean.pkl; if that is not possible the Dashboard column says "n/a" and nothing fails because of it.
Exit code 1 when a must_show figure is missing or any extra check fails.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULT = ROOT / "sample_data" / "demo" / "sales_run" / "result.json"
DEFAULT_EXPECTED = ROOT / "sample_data" / "demo" / "expected.json"

UP = r"upward|increas|growing|growth|rising|rises|climb|\bup\b"
DOWN = r"downward|decreas|declin|shrink|falling|falls|drop|\bdown\b"
NUM = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?\s?([KkMm]\b)?\s?(%)?")


# ---------------------------------------------------------------- text per tab
def _strings(x, skip=("series", "rows", "chart", "charts")) -> str:
    """All the words and numbers a person could read in a JSON value (not raw chart series)."""
    if isinstance(x, dict):
        return " ".join(_strings(v, skip) for k, v in x.items() if k not in skip)
    if isinstance(x, (list, tuple)):
        return " ".join(_strings(v, skip) for v in x)
    return "" if x is None else str(x)


def tab_texts(res: dict, run_dir: Path) -> dict:
    report = " ".join([res.get("report_md") or "", _strings(res.get("report_view")), _strings(res.get("key_metrics")),
                       _strings(res.get("summary_facts"))])
    insights = " ".join([_strings(res.get("findings")), _strings(res.get("strategies")), _strings(res.get("goal_answer"))])
    return {"Report": report, "Insights": insights, "Dashboard": dashboard_text(res, run_dir)}


def dashboard_spec(res: dict, run_dir: Path):
    if not (run_dir / "clean.pkl").exists():
        return None
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.dashboard import build_dashboard
        return build_dashboard(run_dir, res)
    except Exception:
        return None


def dashboard_text(res: dict, run_dir: Path) -> str | None:
    spec = dashboard_spec(res, run_dir)
    if not spec:
        return None
    cols, rows = spec["columns"], spec["rows"]
    out = [spec.get("objective") or "", " ".join(m.get("label", "") for m in spec["measures"])]
    if not (rows and spec["measures"]):
        return " ".join(out)
    mi = cols.index(spec["measures"][0]["col"])
    for d in spec["dims"]:
        di, sums = cols.index(d["col"]), {}
        for r in rows:
            if r[di] is not None and r[mi] is not None:
                sums[r[di]] = sums.get(r[di], 0) + r[mi]
        out += [f"{k} {v:,.0f}" for k, v in sums.items()]
    if spec.get("date") and spec["date"] in cols:
        di, per = cols.index(spec["date"]), {}
        for r in rows:
            if r[di] and r[mi] is not None:
                per[r[di][:7]] = per.get(r[di][:7], 0) + r[mi]
        v = [per[k] for k in sorted(per)]
        if len(v) >= 3:
            k = max(len(v) // 3, 1)
            out.append("upward trend over time" if sum(v[-k:]) > sum(v[:k]) else "downward trend over time")
    return " ".join(out)


# ---------------------------------------------------------------- numbers
def numbers(text: str):
    """(value, approx) for every number in the text. '20.4K' is approximate; '26%' also gives 0.26."""
    out = []
    for m in NUM.finditer(text):
        raw = re.sub(r"[^\d.\-]", "", m.group(0).replace(",", "").split("%")[0].rstrip("KkMm "))
        try:
            v = float(raw)
        except ValueError:
            continue
        unit = (m.group(1) or "").lower()
        if unit:
            out.append((v * (1e3 if unit == "k" else 1e6), True))
        else:
            out.append((v, False))
            if m.group(2):
                out.append((v / 100, False))
    return out


def has_number(text: str, e: float, tol_rel=0.0, tol_abs=0.0) -> bool:
    for v, approx in numbers(text):
        tol = max(tol_abs, abs(e) * (0.02 if approx else tol_rel), 0.5 if abs(e) >= 100 and not approx and tol_rel == 0 and tol_abs == 0 else 0)
        if abs(v - e) <= tol + 1e-9:
            return True
    return False


def found_in(f: dict, text: str) -> bool:
    e, kind = f["expected"], f["kind"]
    if kind == "name":
        return re.search(rf"(?<!\w){re.escape(str(e))}(?!\w)", text, re.I) is not None
    if kind == "direction":
        return re.search(UP if e == "up" else DOWN, text, re.I) is not None
    return has_number(text, float(e), f.get("tol_rel", 0.0), f.get("tol_abs", 0.0))


# ---------------------------------------------------------------- the checks
def check_figures(exp: dict, texts: dict):
    rows = []
    for f in exp["figures"]:
        if f["tabs"] == ["Cleaning log"]:
            continue  # checked by check_cleaning
        tabs = [t for t in ("Report", "Insights", "Dashboard") if texts.get(t) is not None and found_in(f, texts[t])]
        rows.append({"id": f["id"], "label": f["label"], "expected": f["expected"], "must_show": f["must_show"],
                     "found_report": "Report" in tabs, "found_any": bool(tabs), "tabs": tabs})
    return rows


def _charts(res: dict, run_dir: Path):
    out = []
    for f in res.get("findings") or []:
        if f.get("chart"):
            out.append({"id": f.get("id"), "title": f.get("title"), "file": f["chart"], "series": f.get("series"),
                        "sig": (f.get("tool"), json.dumps(f.get("params"), sort_keys=True, default=str)), "finding": f})
    return out


def check_charts(res: dict, run_dir: Path):
    problems, ch = [], _charts(res, run_dir)
    for c in ch:
        if "series" in c["finding"]:
            s = c["series"]
            vals = [p[-1] if isinstance(p, (list, tuple)) else p for p in (s or [])]
            if not vals or all(v is None for v in vals):
                problems.append(f"chart {c['id']} ({c['title']}): its data is empty or null")
        if run_dir.exists() and not (run_dir / c["file"]).exists():
            problems.append(f"chart {c['id']}: file {c['file']} is missing")
    for key in ("sig", "file"):
        seen = {}
        for c in ch:
            k = c[key] if key == "file" else c["sig"]
            if k in seen:
                problems.append(f"charts {seen[k]} and {c['id']} show the same thing ({'same file' if key == 'file' else 'same analysis and settings'})")
            else:
                seen[k] = c["id"]
    return problems


def check_titles(res: dict):
    cols = [c["name"] for c in (res.get("profile") or {}).get("columns", [])]
    titles = [(f.get("id"), f.get("title")) for f in res.get("findings") or []]
    titles += [(f.get("id"), (f.get("insight") or {}).get("headline")) for f in res.get("findings") or []]
    titles += [(s.get("id"), s.get("title")) for s in res.get("strategies") or []]
    bad = []
    for i, t in titles:
        if not t:
            continue
        raw = [c for c in cols if "_" in c and c in t]
        if raw:
            bad.append(f"{i}: raw column name '{raw[0]}' in title '{t}'")
        elif "_" in t:
            bad.append(f"{i}: underscore in title '{t}'")
    return bad


_DATE = re.compile(r"\d{4}-\d{2}(-\d{2})?|\b\d{1,2} [A-Z][a-z]{2} \d{4}|\b(19|20)\d{2}\b|\[[a-z]\d+\]|\bp ?= ?[\d.]+|\bf\d+\b|\br\d+\b", re.I)


def known_numbers(res: dict) -> list:
    """Every number the run itself computed, in any form a sentence might show it."""
    pool = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            pool.add(float(x))
        elif isinstance(x, str):
            for v, _ in numbers(x):
                pool.add(v)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    # numbers the code computed: not the sentences under test (a caption must not vouch for itself)
    for f in res.get("findings") or []:
        walk({k: f.get(k) for k in ("stats", "series", "params", "date_coverage", "round")})
    for k in ("summary_facts", "key_metrics", "profile", "cleaning_log", "derived_metrics"):
        walk(res.get(k))
    extra = set()
    for v in pool:
        extra |= {v * 100, v / 100, abs(v), v * 1000, v / 1000}
    return sorted(pool | extra)


def check_captions(res: dict):
    pool = known_numbers(res)
    bad = []
    for f in res.get("findings") or []:
        caption = " ".join(str(x) for x in (f.get("text"), (f.get("insight") or {}).get("why_it_matters"), f.get("chart_why")) if x)
        for v, approx in numbers(_DATE.sub(" ", caption)):
            shown = re.search(rf"{re.escape(f'{abs(v):g}'.split('.')[0])}(\.\d+)?", caption)
            decimals = len(shown.group(1)) - 1 if shown and shown.group(1) else 0
            tol = max(abs(v) * (0.02 if approx else 0.006), 0.5 * 10 ** -decimals + 1e-9)  # a figure rounded to N places may be off by half a place
            if not any(abs(v - p) <= tol for p in pool):
                bad.append(f"{f.get('id')}: number {v:g} in the caption is not in the run's data")
    return bad


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def check_cleaning(res: dict, exp: dict):
    d, bad = exp["details"], []
    log = res.get("cleaning_log") or []
    planted = sum(d["duplicate_rows_removed"].values())
    removed = sum(int(s.get("count") or 0) for s in log if re.search(r"duplicate", s.get("step", ""), re.I))
    if removed != planted:
        bad.append(f"cleaning log removes {removed} duplicate rows; {planted} were planted ({d['duplicate_rows_removed']})")
    cols = {c["name"]: c for c in (res.get("profile") or {}).get("columns", [])}
    logtxt = _strings(log).lower()
    for table, per in d["missing_values"].items():
        for col, n in per.items():
            c = cols.get(col)
            if c is None:
                continue  # dropped or renamed by the join; nothing to compare
            if not (c.get("missing_pct", 0) > 0 or col.lower() in logtxt):
                bad.append(f"{table}.{col}: {n} missing values planted, but neither the profile nor the cleaning log shows any")
    return bad


def check_joins(res: dict, exp: dict):
    dm = res.get("data_model")
    if not dm:
        return ["the run has no data_model: the files were not joined"]
    bad, real = [], {(_norm(l["child"]), _norm(l["child_col"]), _norm(l["parent"]), _norm(l["parent_col"])) for l in exp["details"]["links"]}
    used = set()
    for r in dm.get("relationships") or []:
        k = (_norm(r["child_table"]), _norm(r["child_col"]), _norm(r["parent_table"]), _norm(r["parent_col"]))
        rev = (k[2], k[3], k[0], k[1])
        if r.get("status") == "used":
            if k in real or rev in real:
                used.add(k if k in real else rev)
            else:
                bad.append(f"join used on a pair that is not a real key: {r['child_table']}.{r['child_col']} -> {r['parent_table']}.{r['parent_col']}")
    for l in exp["details"]["links"]:
        k = (_norm(l["child"]), _norm(l["child_col"]), _norm(l["parent"]), _norm(l["parent_col"]))
        if k not in used:
            bad.append(f"real key not used by the run: {l['child']}.{l['child_col']} -> {l['parent']}.{l['parent_col']}")
    return bad


# ---------------------------------------------------------------- main
def run(result_path: Path, expected_path: Path) -> int:
    res = json.loads(Path(result_path).read_text(encoding="utf-8"))
    exp = json.loads(Path(expected_path).read_text(encoding="utf-8"))
    run_dir = Path(result_path).parent
    texts = tab_texts(res, run_dir)
    rows = check_figures(exp, texts)

    w = max(len(r["label"]) for r in rows) + 2
    print(f"{'figure':<{w}}{'expected':<14}{'in report':<11}{'tab(s)'}   (* = must show)")
    for r in rows:
        mark = "*" if r["must_show"] else " "
        print(f"{r['label']:<{w}}{str(r['expected'])[:12]:<14}{'yes' if r['found_report'] else 'no':<11}{', '.join(r['tabs']) or '-'} {mark}")
    if texts["Dashboard"] is None:
        print("Dashboard tab: n/a (could not be rebuilt from this run folder)")

    failed = [r for r in rows if r["must_show"] and not r["found_any"]]
    extra = {"charts": check_charts(res, run_dir), "titles": check_titles(res), "captions": check_captions(res),
             "cleaning log": check_cleaning(res, exp), "joins": check_joins(res, exp)}
    print()
    for name, probs in extra.items():
        print(f"{name}: {'ok' if not probs else str(len(probs)) + ' problem(s)'}")
        for p in probs:
            print("   -", p)
    n_bad = len(failed) + sum(len(p) for p in extra.values())
    print(f"\n{'FAILED' if n_bad else 'PASSED'}: {len(failed)} must_show figure(s) missing, {n_bad - len(failed)} other problem(s)")
    return 1 if n_bad else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.exit(run(Path(a[0]) if a else DEFAULT_RESULT, Path(a[1]) if len(a) > 1 else DEFAULT_EXPECTED))
