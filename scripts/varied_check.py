"""Runs each varied synthetic export (tests/fixtures/varied/, made by scripts/make_varied_datasets.py) through the pipeline in rules-only mode, answers every question with its default, and checks the result:

    no_crash         the run finished (status done)
    understanding    roles assigned; dates, money, quantity, status and identifiers recognised where the file has them; personal columns not read as measures
    long_format      several rows per order do not inflate order counts or totals (files with such a layout)
    no_double_count  totals in the cleaned table equal the file's own totals: no fee, waiver, refund or summary row counted twice or dropped
    report           a report was produced and scripts/report_check.py passes on the run
    data_card        the Lens data card builds, holds no raw row and lists no value of a personal-looking column
    custom_analysis  a SQL question over the cleaned table runs (type handling), also through Lens with the fake AI
    time_memory      seconds and peak memory of the run, within the limits below

    python scripts/varied_check.py                 # all files, one process each (so the memory is the file's own); prints a table and writes a JSON next to docs/compatibility.md
    python scripts/varied_check.py --one <file>    # one file, JSON on the last line
Nothing calls an AI: the pipeline runs rules-only and Lens uses the fake AI."""
from __future__ import annotations

import argparse
import json
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "backend" / "tests" / "fixtures" / "varied"
MAX_SECONDS, MAX_MB = 120, 1500
sys.path.insert(0, str(ROOT / "backend"))

# What each file contains, by its own header text (test data, not product code).
EXPECT = {
    "a_shopify_orders.csv": dict(date=["Created at", "Paid at"], money=["Total", "Subtotal", "Lineitem price"], quantity=["Lineitem quantity"], status=["Financial Status"], ids=["Name", "Id"],
                                 personal=["Email", "Billing Name", "Billing Phone", "Phone", "Billing Street", "Shipping Name", "Shipping Phone"], order_key="Name", orders=320, layout="documented"),
    "b_flipkart_payout.xlsx": dict(date=["Order Date"], money=["Sale Amount", "Commission Fee", "Net Settlement"], quantity=["Quantity"], status=["Order Item Status"], ids=["Order ID"], personal=[],
                                   order_key="Order ID", orders=260, layout="documented", table="Orders", reconcile={"Sale Amount": None}),
    "c_amazon_settlement.txt": dict(date=["posted-date"], money=["amount"], quantity=["quantity-purchased"], status=["transaction-type"], ids=["order-id"], personal=[], order_key="order-id", orders=240,
                                    layout="typical pattern", reconcile={"amount": "summary:total-amount"}),
    "d_tally_gst_sales_register.xlsx": dict(date=["Date"], money=["Taxable Value", "Gross Total"], quantity=["Quantity"], status=["Voucher Type"], ids=["Voucher No."], personal=["GSTIN/UIN", "Particulars"],
                                            layout="typical pattern", reconcile={"Taxable Value": "totals-row", "Gross Total": "totals-row"}),
    "e_hindi_headers.csv": dict(date=["Tarikh"], money=["Kul rashi", "Dar (Rate)"], quantity=["Matra"], status=["Bhugtan sthiti"], ids=["Bill No"], personal=["Grahak ka naam", "Mobile no."], layout="typical pattern"),
    "f_wide_80_columns.csv": dict(date=["order_date"], money=["revenue"], quantity=["quantity"], status=["status"], ids=["record_id"], personal=[], layout="typical pattern", columns=80),
    "g_mixed_dates_amounts.csv": dict(date=["Date"], money=["Amount"], quantity=[], status=[], ids=["Invoice"], personal=["Customer"], layout="typical pattern", amounts="Amount", rows=260),
    "h_quantities_no_money.csv": dict(date=["order_date"], money=[], quantity=["quantity", "returned_qty"], status=["status"], ids=["order_id"], personal=[], layout="typical pattern"),
    "i_messy_headers.csv": dict(date=["Date"], money=["Amount"], quantity=["Qty"], status=["Status"], ids=["Order No"], personal=["Customer", "Phone", "Email"], layout="typical pattern", rows=220),
}
CHECKS = ["no_crash", "understanding", "long_format", "no_double_count", "report", "data_card", "custom_analysis", "time_memory"]
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z0-9][A-Z0-9]\b")


def _norm(name: str) -> str:
    return str(name).strip().lower()


def run_one(name: str) -> dict:
    os.environ["LLM_PROVIDER"] = "none"
    tmp = tempfile.mkdtemp()
    os.environ.update(RUNS_DIR=tmp + "/runs", AI_CACHE_DIR=tmp + "/c", CHART_DPI="50", GLOSSARY_PATH=tmp + "/g.json")
    import pandas as pd
    from app import runner
    exp = EXPECT[name]
    out: dict = {"file": name, "layout": exp["layout"], "checks": {}, "notes": {}}
    t0 = time.time()
    rid = runner.create_run("", [(name, (FIX / name).read_bytes())])
    wd = runner.run_dir(rid)
    try:
        runner._execute(rid)
        st = runner.read_status(rid) or {}
        if st.get("status") == "needs_input":                     # answer every question with its default, as a person who just clicks Continue would
            qs = json.loads((wd / "questions.json").read_text(encoding="utf-8"))["questions"]
            (wd / "answers.json").write_text(json.dumps({"answers": {q["id"]: q["default"] for q in qs}, "skip": False, "notes": ""}))
            out["notes"]["questions_answered_with_defaults"] = len(qs)
            runner._execute(rid)
            st = runner.read_status(rid) or {}
    except Exception as e:                                          # noqa: BLE001  a crash is the result, not a reason to stop the other checks
        st = {"status": "crashed", "error": f"{type(e).__name__}: {e}"}
    out["seconds"] = round(time.time() - t0, 1)
    out["peak_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    ok = st.get("status") == "done"
    out["checks"]["no_crash"] = {"ok": ok, "detail": "" if ok else f"{st.get('status')}: {str(st.get('error', ''))[:300]}"}
    out["checks"]["time_memory"] = {"ok": out["seconds"] <= MAX_SECONDS and out["peak_mb"] <= MAX_MB, "detail": f"{out['seconds']} s, {out['peak_mb']} MB peak"}
    if not ok:
        for c in CHECKS:
            out["checks"].setdefault(c, {"ok": False, "detail": "the run did not finish"})
        return out
    result = json.loads((wd / "result.json").read_text())
    df = pd.read_pickle(wd / "clean.pkl")
    prof = {c["name"]: c for c in result["profile"]["columns"]}
    out["rows"], out["columns"] = int(len(df)), int(df.shape[1])
    low = {_norm(c): c for c in df.columns}
    find = lambda h: low.get(_norm(h))

    # ---- understanding
    bad = []
    for h in exp["date"]:
        c = find(h)
        if not c or not pd.api.types.is_datetime64_any_dtype(df[c]):
            bad.append(f"{h}: not read as a date ({df[c].dtype if c else 'column missing'})")
    for h in exp["money"] + exp["quantity"]:
        c = find(h)
        if not c or not pd.api.types.is_numeric_dtype(df[c]) or prof.get(c, {}).get("role") in ("id", "text", "empty"):
            bad.append(f"{h}: not read as a number ({df[c].dtype if c else 'column missing'}, role {prof.get(c, {}).get('role')})")
    for h in exp["status"]:
        c = find(h)
        if not c or prof.get(c, {}).get("role") not in ("categorical", "binary"):
            bad.append(f"{h}: not read as a status ({prof.get(c, {}).get('role') if c else 'column missing'})")
    for h in exp["ids"]:
        c = find(h)
        if not c or prof.get(c, {}).get("role") not in ("id", "text", "categorical"):
            bad.append(f"{h}: read as a measure ({prof.get(c, {}).get('role') if c else 'column missing'})")
    for h in exp["personal"]:
        c = find(h)
        if c and prof.get(c, {}).get("role") in ("numeric", "date"):
            bad.append(f"{h}: a personal column read as a {prof[c]['role']}")
    if "columns" in exp and df.shape[1] < exp["columns"]:
        bad.append(f"only {df.shape[1]} of {exp['columns']} columns kept")
    if "rows" in exp and len(df) != exp["rows"]:
        bad.append(f"{len(df)} rows, expected {exp['rows']}")
    if exp.get("amounts"):
        c = find(exp["amounts"])
        if c is not None and pd.api.types.is_numeric_dtype(df[c]):
            raw = pd.read_csv(FIX / name)[exp["amounts"]]
            if df[c].notna().sum() < 0.95 * len(raw):
                bad.append(f"{exp['amounts']}: only {df[c].notna().sum()} of {len(raw)} amounts parsed")
    out["checks"]["understanding"] = {"ok": not bad, "detail": "; ".join(bad)[:400]}

    # ---- long format and totals
    raw_totals = _raw_totals(name, exp)
    key = exp.get("order_key")
    bad = []
    if key:
        kc = find(key)
        if kc is None:
            bad.append(f"order key {key} missing")
        else:
            n_orders = int(df[kc].nunique(dropna=True))
            if n_orders != exp["orders"]:
                bad.append(f"{n_orders} distinct orders, expected {exp['orders']}")
            out["notes"]["rows_per_order"] = round(len(df) / max(n_orders, 1), 2)
            text = (wd / "report.md").read_text(encoding="utf-8").lower() if (wd / "report.md").exists() else ""
            if len(df) != n_orders and re.search(rf"\b{len(df)}\s+orders\b", text):
                bad.append(f"the report calls {len(df)} rows 'orders'")
            for h in exp["money"]:
                c = find(h)
                if c is not None and raw_totals.get(h) is not None:          # an order-level amount repeated on each line item must be counted once per order
                    if abs(float(df[c].sum()) - raw_totals[h]) > 0.01 * abs(raw_totals[h]) + 1:
                        bad.append(f"{h}: total {df[c].sum():,.2f} vs the file's {raw_totals[h]:,.2f}")
    out["checks"]["long_format"] = {"ok": not bad, "detail": "; ".join(bad)[:400]} if key else {"ok": True, "detail": "n/a (one row per record)"}
    bad = []
    for h, how in (exp.get("reconcile") or {}).items():
        c = find(h)
        want = raw_totals.get(h)
        if c is None or want is None:
            bad.append(f"{h}: no column or no reference total")
        elif abs(float(df[c].sum()) - want) > 0.01 * abs(want) + 1:
            bad.append(f"{h}: total {df[c].sum():,.2f}, the file says {want:,.2f}")
    if name.startswith("b_"):                              # the returns and cancellations sheets are not rows of the orders table
        t = pd.read_excel(FIX / name, sheet_name="Orders")
        if len(df) != len(t):
            bad.append(f"{len(df)} rows, the Orders sheet has {len(t)}")
        fee = find("Commission Fee")
        if fee is not None and abs(float(df[fee].sum()) - float(t["Commission Fee"].sum())) > 1:
            bad.append("commission fee total differs from the sheet")
    out["checks"]["no_double_count"] = {"ok": not bad, "detail": "; ".join(bad)[:400]} if (exp.get("reconcile") or name.startswith("b_")) else {"ok": True, "detail": "n/a (no fees, refunds or totals rows in this file)"}

    # ---- report
    rep_ok = bool(result.get("report_md")) and (wd / "report.md").exists()
    rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "report_check.py"), str(wd)], capture_output=True, text=True, timeout=300)
    fails = [x for x in rc.stdout.splitlines() if x.startswith("FAIL")]
    out["checks"]["report"] = {"ok": rep_ok and rc.returncode == 0, "detail": ("" if rep_ok else "no report; ") + "; ".join(fails)[:300]}

    # ---- data card
    from app.lens import chat, store
    from app.lens.agent import datacard
    ldf, ctx = chat.load_data(wd, result)
    card = datacard.get(ctx, ldf)
    text, _ = datacard.render(card, "what is in the data", "")
    bad = []
    if EMAIL.search(text) or GSTIN.search(text):
        bad.append("an e-mail address or GSTIN is in the card")
    for h in exp["personal"]:
        c = find(h)
        if c is None:
            continue
        vals = ldf[c].dropna().astype(str).unique()[:200]
        leaked = [v for v in vals if len(v) >= 6 and v in text]
        if leaked:
            bad.append(f"{h}: values listed ({leaked[:2]})")
    rows_in_card = sum(1 for r in ldf.head(30).itertuples(index=False) if all(str(v) in text for v in list(r)[:4] if len(str(v)) > 5 and not pd.isna(v)))
    if rows_in_card:
        bad.append("a raw row is in the card")
    out["checks"]["data_card"] = {"ok": not bad, "detail": "; ".join(bad)[:300]}

    # ---- custom_analysis
    from app.lens.agent import sqlguard
    bad = []
    try:
        meas = [c.name for c in ctx.cols.values() if c.role == "measure"]
        cats = [c.name for c in ctx.cols.values() if c.role in ("category", "flag")]
        q = lambda n: '"' + n.replace('"', '""') + '"'
        sqls = ["SELECT COUNT(*) AS n FROM data"]
        if meas:
            sqls.append(f"SELECT AVG({q(meas[0])}) AS a, MEDIAN({q(meas[0])}) AS m FROM data")
        if meas and cats:
            sqls.append(f"SELECT {q(cats[0])} AS g, SUM({q(meas[0])}) AS s FROM data GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
        dates = [c.name for c in ctx.cols.values() if c.role == "date"]
        if dates:
            sqls.append(f"SELECT strftime({q(dates[0])}, '%Y-%m') AS ym, COUNT(*) AS n FROM data GROUP BY 1 ORDER BY 1")
        for sql in sqls:
            r = sqlguard.execute(sqlguard.validate(sql, list(ldf.columns)), ldf)
            if not getattr(r, "rows", None) and not hasattr(r, "columns"):
                bad.append(f"no result for {sql[:40]}")
    except Exception as e:           # noqa: BLE001
        bad.append(f"{type(e).__name__}: {str(e)[:200]}")
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        from tests.test_lens import FakeAI
        sql = f"SELECT COUNT(*) AS n FROM data"
        ai = FakeAI({"plan": [{"tool": "custom_analysis", "args": {"sql": sql, "description": "how many records the table holds"}}]})
        a = chat.ask(wd, result, question="how many records are there", llm=ai, allow_ai=True, tab="Report")["answer"]
        if a.get("status") != "answered" or "failed unexpectedly" in str(a.get("text")):
            bad.append(f"through Lens: {a.get('status')}: {str(a.get('text'))[:120]}")
    except Exception as e:           # noqa: BLE001
        bad.append(f"through Lens: {type(e).__name__}: {str(e)[:160]}")
    out["checks"]["custom_analysis"] = {"ok": not bad, "detail": "; ".join(bad)[:300]}
    out["cleaning_log"] = [f"{c.get('step')}: {c.get('detail')}"[:160] for c in (result.get("cleaning_log") or [])][:12]
    return out


def _raw_totals(name: str, exp: dict) -> dict:
    """The totals the file itself states (a totals row, a summary row) or that its data rows add up to, read straight from the file: what the cleaned table has to equal."""
    import pandas as pd
    t: dict = {}
    if name.startswith("d_"):
        raw = pd.read_excel(FIX / name, header=None)
        last = raw.iloc[-1]
        hdr = raw.iloc[4]
        for i, h in enumerate(hdr):
            t[str(h)] = float(last.iloc[i]) if str(h) in ("Taxable Value", "Gross Total", "CGST", "SGST", "IGST", "Quantity") and pd.notna(last.iloc[i]) else None
    elif name.startswith("c_"):
        raw = pd.read_csv(FIX / name, sep="\t")
        t["amount"] = float(raw["total-amount"].dropna().iloc[0])
    elif name.startswith("b_"):
        raw = pd.read_excel(FIX / name, sheet_name="Orders")
        t["Sale Amount"] = float(raw["Sale Amount"].sum())
        t["Commission Fee"] = float(raw["Commission Fee"].sum())
        t["Net Settlement"] = float(raw["Net Settlement"].sum())
    elif name.startswith("a_"):
        raw = pd.read_csv(FIX / name)
        first = raw.drop_duplicates("Name")
        t["Total"] = float(first["Total"].sum())
        t["Subtotal"] = float(first["Subtotal"].sum())
        t["Lineitem price"] = float(raw["Lineitem price"].sum())
    return t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--one")
    ap.add_argument("--out", default=str(ROOT / "docs" / "compatibility_results.json"))
    a = ap.parse_args(argv)
    if a.one:
        print(json.dumps(run_one(a.one)))
        return 0
    results = []
    for name in EXPECT:
        p = subprocess.run([sys.executable, __file__, "--one", name], capture_output=True, text=True, timeout=900, cwd=ROOT / "backend")
        line = [x for x in p.stdout.splitlines() if x.startswith("{")]
        results.append(json.loads(line[-1]) if line else {"file": name, "layout": EXPECT[name]["layout"], "checks": {c: {"ok": False, "detail": "the checker crashed: " + p.stderr[-300:]} for c in CHECKS}})
    Path(a.out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"{'file':34s} " + " ".join(f"{c[:9]:>9s}" for c in CHECKS))
    for r in results:
        print(f"{r['file']:34s} " + " ".join(f"{('PASS' if r['checks'].get(c, {}).get('ok') else 'FAIL'):>9s}" for c in CHECKS))
        for c in CHECKS:
            d = r["checks"].get(c, {})
            if not d.get("ok"):
                print(f"    {c}: {d.get('detail')}")
    return 0 if all(v["ok"] for r in results for v in r["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
