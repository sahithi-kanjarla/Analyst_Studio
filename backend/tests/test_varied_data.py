"""Files from real-world export styles: the synthetic generator, every file through the pipeline (rules-only, fake AI for Lens), and a test for each general fix the files showed was needed."""
import datetime as dt
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

from app import grain, measures, profiler, tableio
from app.lens.agent import datacard

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "backend" / "tests" / "fixtures" / "varied"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


maker = _load("make_varied_datasets")
check = _load("varied_check")


# ------------------------------------------------------------------ the generator
def test_the_generator_is_deterministic_and_says_documented_or_typical(tmp_path, monkeypatch):
    def snapshot(d):
        out = {}
        for p in sorted(d.iterdir()):
            if p.suffix == ".xlsx":
                wb = openpyxl.load_workbook(p, data_only=True)
                out[p.name] = hashlib.md5(json.dumps([[list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets], default=str).encode()).hexdigest()
            else:
                out[p.name] = hashlib.md5(p.read_bytes()).hexdigest()
        return out
    monkeypatch.setattr(maker, "OUT", tmp_path / "one")
    maker.main()
    monkeypatch.setattr(maker, "OUT", tmp_path / "two")
    maker.main()
    assert snapshot(tmp_path / "one") == snapshot(tmp_path / "two")
    readme = (tmp_path / "one" / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in (tmp_path / "one").iterdir() if p.name != "README.md")
    assert len(files) == 9 and set(files) == set(check.EXPECT)
    for f in files:
        line = next(x for x in readme.splitlines() if f"`{f}`" in x)
        assert "**documented**" in line or "**typical pattern**" in line
    assert sum("**documented**" in x for x in readme.splitlines()) == 2             # the Shopify-style and the Flipkart-style files only


def test_the_generated_files_hold_no_real_personal_data():
    shopify = pd.read_csv(FIX / "a_shopify_orders.csv", dtype=str)
    assert shopify["Email"].dropna().str.endswith("@example.com").all()
    assert shopify["Billing Phone"].dropna().str.fullmatch(r"[6-9]\d{9}").all()
    tally = pd.read_excel(FIX / "d_tally_gst_sales_register.xlsx", header=4)
    gst = tally["GSTIN/UIN"].dropna().astype(str)
    assert gst.iloc[:-1].str.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z0-9][A-Z0-9]").all()
    first = {n.split()[0] for n in shopify["Billing Name"].dropna()}
    assert first <= set(maker.FIRST)                                              # made-up names from the generator's own lists


def test_each_layout_has_the_features_it_is_meant_to_have():
    a = pd.read_csv(FIX / "a_shopify_orders.csv")
    assert a.shape[1] >= 50 and len(a) > a["Name"].nunique() and a.groupby("Name")["Total"].count().max() == 1       # one row per line item, order fields on the first row only
    assert re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}", str(a["Created at"].iloc[0]))
    wb = openpyxl.load_workbook(FIX / "b_flipkart_payout.xlsx")
    assert wb.sheetnames == ["Help", "Orders", "Returns", "Cancellations"]
    orders = pd.read_excel(FIX / "b_flipkart_payout.xlsx", sheet_name="Orders")
    fees = [c for c in orders.columns if c.endswith("Fee")]
    assert fees and all(f"{f} Waiver" in orders.columns for f in fees)
    c = pd.read_csv(FIX / "c_amazon_settlement.txt", sep="\t")
    assert pd.notna(c["total-amount"].iloc[0]) and c["amount"].iloc[0] != c["amount"].iloc[0] and (c["amount"] < 0).any() and set(c["transaction-type"].dropna()) == {"Order", "Refund"}
    d = openpyxl.load_workbook(FIX / "d_tally_gst_sales_register.xlsx").active
    assert d["A1"].value and d["A5"].value == "Date" and str(d.cell(d.max_row, 1).value) == "Grand Total" and d.merged_cells.ranges
    assert pd.read_csv(FIX / "f_wide_80_columns.csv").shape[1] == 80
    assert not [c for c in pd.read_csv(FIX / "h_quantities_no_money.csv").columns if re.search(r"amount|price|revenue|total|rate", c, re.I)]
    i = (FIX / "i_messy_headers.csv").read_text(encoding="utf-8")
    assert re.search(r"\dE\+\d\d", i) and "N/A" in i and i.splitlines()[0].count(",") >= 12


# ------------------------------------------------------------------ every file through the pipeline
@pytest.fixture(scope="module")
def results():
    out = {}
    for name in check.EXPECT:
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "varied_check.py"), "--one", name], capture_output=True, text=True, timeout=900, cwd=ROOT / "backend")
        line = [x for x in p.stdout.splitlines() if x.startswith("{")]
        assert line, p.stdout[-500:] + p.stderr[-800:]
        out[name] = json.loads(line[-1])
    return out


@pytest.mark.parametrize("name", list(check.EXPECT))
def test_the_file_goes_through_the_whole_pipeline(results, name):
    failed = {c: v["detail"] for c, v in results[name]["checks"].items() if not v["ok"]}
    assert not failed, failed
    assert results[name]["seconds"] <= check.MAX_SECONDS and results[name]["peak_mb"] <= check.MAX_MB


# ------------------------------------------------------------------ the fixes, one by one
def csv_bytes(rows, sep=",", enc="utf-8"):
    buf = io.StringIO()
    import csv
    csv.writer(buf, delimiter=sep).writerows(rows)
    return buf.getvalue().encode(enc)


TABLE = [["Item", "Qty", "Amount"]] + [[f"x{i}", i % 4 + 1, 100 + i] for i in range(30)]


def test_title_rows_above_the_header_are_skipped_and_said():
    rows = [["Acme Traders"], ["Sales Register"], ["1 Apr to 31 Mar"], [], *TABLE]
    df = tableio.read_delimited(csv_bytes(rows))
    assert list(df.columns) == ["Item", "Qty", "Amount"] and len(df) == 30
    assert df.attrs["load_notes"][0]["step"] == "Skipped title rows" and "row 5" in df.attrs["load_notes"][0]["detail"]
    assert tableio.header_row([["only one cell"], ["a", "b", "c"], ["1", "2", "3"]]) == 1
    assert tableio.header_row([["1", "2"], ["3", "4"], ["5", "6"]]) == 0                         # no header at all: the first row, as before


@pytest.mark.parametrize("enc,sep", [("utf-8-sig", ","), ("utf-16", "\t"), ("cp1252", ";"), ("utf-8", "|"), ("utf-8", "\t")])
def test_encodings_and_delimiters(enc, sep):
    rows = [["Naam", "Shehar", "Rashi"]] + [[f"Zoë {i}" if enc != "utf-16" else f"Anu {i}", "Pune", 100 + i] for i in range(25)]
    df = tableio.read_delimited(csv_bytes(rows, sep, enc))
    assert list(df.columns) == ["Naam", "Shehar", "Rashi"] and len(df) == 25 and df["Rashi"].sum() == sum(100 + i for i in range(25))
    if enc == "cp1252":
        assert df["Naam"].iloc[0] == "Zoë 0" and any("not UTF-8" in n["detail"] for n in df.attrs["load_notes"])


def test_a_totals_row_is_removed_by_its_label_and_by_its_sums():
    labelled = pd.DataFrame({"Item": [f"x{i}" for i in range(12)] + ["Grand Total"], "Qty": list(range(1, 13)) + [78], "Amount": list(range(10, 130, 10)) + [780]})
    out = tableio.drop_total_rows(labelled.copy(), notes := [])
    assert len(out) == 12 and out["Amount"].sum() == 780 and notes[0]["step"] == "Removed totals rows"
    unlabelled = pd.DataFrame({"Item": [f"x{i}" for i in range(12)] + [None], "Qty": list(range(1, 13)) + [78], "Amount": list(range(10, 130, 10)) + [780]})
    assert len(tableio.drop_total_rows(unlabelled, [])) == 12
    keep = pd.DataFrame({"Item": [f"x{i}" for i in range(12)], "Qty": range(12), "Amount": range(12)})
    assert len(tableio.drop_total_rows(keep, [])) == 12                          # nothing that is not a total goes
    sub = pd.DataFrame({"Item": ["a", "b", "Sub Total", "c", "d", "Total"], "Amount": [1, 2, 3, 4, 5, 15]})
    assert list(tableio.drop_total_rows(sub, [])["Item"]) == ["a", "b", "c", "d"]


def test_a_summary_row_inside_the_data_is_removed_with_the_columns_only_it_filled():
    df = pd.DataFrame({"settlement": [None] * 8, "total": [None] * 8, "type": ["Order"] * 8, "amount": [5.0, -1, 7, -2, 9, 3, 4, 8]})
    df.loc[-1] = ["S1", 99.0, None, None]
    df = df.sort_index().reset_index(drop=True)
    out = tableio.drop_summary_rows(df, notes := [])
    assert len(out) == 8 and list(out.columns) == ["type", "amount"] and "settlement = S1" in notes[0]["detail"] and out["amount"].sum() == 33


def test_empty_columns_and_duplicate_and_blank_headers():
    df = pd.DataFrame([[1, 2, 3, None, 5, None]] * 6, columns=["Amount", "Amount ", "", "Empty", " ", "Unnamed: 5"])
    out = tableio.finish(df, [])
    assert list(out.columns) == ["Amount", "Amount_2", "column_3", "column_5"] or list(out.columns) == ["Amount", "Amount_2", "column_3"] or "Amount_2" in out.columns
    assert "Empty" not in out.columns and any(n["step"] == "Left out empty columns" for n in out.attrs["load_notes"])
    assert tableio.clean_headers(["A", "A ", None, "A"]) == ["A", "A_2", "column_3", "A_3"]


def test_merged_cells_down_a_group_are_filled_and_title_merges_are_left(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Regional sales"])
    ws.append(["Region", "Product", "Amount"])
    for r in ("North", "South"):
        for p in range(8):
            ws.append([r if p == 0 else None, f"P{p}", 10 + p])
    ws.merge_cells("A1:C1")
    ws.merge_cells("A3:A10")
    ws.merge_cells("A11:A18")
    path = tmp_path / "m.xlsx"
    wb.save(path)
    df = list(tableio.read_workbook(path).values())[0]
    assert list(df.columns) == ["Region", "Product", "Amount"] and df["Region"].tolist() == ["North"] * 8 + ["South"] * 8


def test_a_sheet_of_notes_is_left_out_and_a_real_sheet_kept(tmp_path):
    path = tmp_path / "w.xlsx"
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame([["Read this first. Each row below is one order item and each fee has a waiver column."], ["Net = sale minus fees plus waivers, and it is zero for items not delivered."]]).to_excel(xw, sheet_name="Help", index=False, header=False)
        pd.DataFrame(TABLE[1:], columns=TABLE[0]).to_excel(xw, sheet_name="Data", index=False)
    sheets = tableio.read_workbook(path)
    assert list(sheets) == ["Data"] and any("Help" in n["detail"] for n in sheets["Data"].attrs["load_notes"])


def test_digit_strings_with_leading_zeros_stay_text():
    rows = [["Code", "Amount"]] + [[f"{i % 5:05d}", 10 + i] for i in range(30)]
    df = tableio.read_delimited(csv_bytes(rows))
    assert df["Code"].iloc[0] == "00000" and df["Code"].iloc[1] == "00001" and pd.api.types.is_numeric_dtype(df["Amount"])


def test_uneven_rows_do_not_stop_the_read():
    rows = [["a", "b", "c"]] + [[str(i), str(i + 1), str(i + 2)] for i in range(20)] + [["1", "2", "3", "4"]]
    df = tableio.read_delimited(csv_bytes(rows))
    assert len(df) == 21 and list(df.columns)[:3] == ["a", "b", "c"]


def test_utc_offsets_keep_the_clock_time_as_written():
    s = pd.Series(["2024-03-05 14:22:10 +0530", "2024-03-05T00:00:00+05:30", "2024-03-05 10:00:00 -0800", "2024-03-05 10:00:00 UTC", None])
    out = profiler.parse_dates(s)
    assert out.dtype == "datetime64[ns]" and out.iloc[0] == pd.Timestamp("2024-03-05 14:22:10") and out.iloc[2] == pd.Timestamp("2024-03-05 10:00:00") and pd.isna(out.iloc[4])


def test_amounts_written_for_people_become_numbers():
    s = pd.Series(["₹1,25,000", "Rs. 12.5k", "1.25 lakh", "98000", "₹ 7,500.50", "(500)", "2 cr", "3.4 million", "1,234.5-"])
    assert profiler.try_numeric(s, 0.5).tolist() == [125000, 12500, 125000, 98000, 7500.5, -500, 20000000, 3400000, -1234.5]
    assert profiler.try_numeric(pd.Series(["abc", "def", "1"] * 10)) is None


def test_dates_written_in_two_orders_are_each_read_their_own_way_and_flagged():
    d = pd.Series(["03/04/2024", "13/04/2024", "04/25/2024", "5 Apr 2024", "2024-04-03", "04-05-24"])
    out = profiler.parse_dates(d)
    assert out.notna().all() and out.iloc[1] == pd.Timestamp("2024-04-13") and out.iloc[2] == pd.Timestamp("2024-04-25")
    assert profiler.date_order_conflict(d) == (1, 1, 2)


def test_long_numbers_in_scientific_notation_and_phone_numbers_are_ids_not_measures():
    n = 60
    ids = pd.Series([f"{2 + i % 7}.{i % 90 + 10:02d}E+11" for i in range(n)])
    assert profiler.infer_column(ids, "Reference")[0] == "id"
    phones = pd.Series(np.arange(n) * 7919 + 9_800_000_000, dtype="int64")
    assert profiler.infer_column(phones, "Contact")[0] == "id" and profiler.infer_column(phones, "x")[0] == "id"
    assert profiler.infer_column(pd.Series(np.arange(n) % 9 * 1000 + 5000.0), "Amount")[0] == "numeric"
    assert profiler.ID_NAME.search("Order No") and profiler.ID_NAME.search("Voucher No.") and not profiler.ID_NAME.search("Notes") and not profiler.ID_NAME.search("Nov")


def test_local_words_for_amounts_quantities_and_rates():
    for name in ("Kul rashi", "Matra", "Bikri", "Taxable Value", "Commission Fee", "Net Settlement", "Gross Total", "CGST"):
        assert measures.measure_kind(name) == "additive", name
    for name in ("Dar (Rate)", "Chhoot", "Discount", "Unit price"):
        assert measures.measure_kind(name) == "rate", name


def test_the_data_card_lists_no_value_of_gstin_address_or_contact_columns():
    n = 120
    rng = np.random.RandomState(1)
    gst = [f"{rng.randint(10, 36)}ABCDE{rng.randint(1000, 9999)}F1Z{rng.randint(0, 9)}" for _ in range(n)]
    s = pd.Series(gst)
    assert datacard.personal_looking("Tax ref", "Tax ref", s, n) == "it looks like tax registration numbers"
    for name in ("Billing Address1", "GSTIN/UIN", "Mobile no.", "Grahak ka naam", "Pincode"):
        assert datacard.personal_looking(name, name, pd.Series(["a", "b", "c"] * 40), n), name


def test_several_rows_per_order_are_counted_by_order_and_repeated_totals_once():
    rows = []
    for o in range(50):
        for k in range(1 + o % 3):
            rows.append({"Order ID": f"A{o:03d}", "Order Total": 100 + o, "Line price": 10 + k, "Qty": 1 + k})
    df = pd.DataFrame(rows)
    g = grain.detect(df)
    assert g["orders"] == 50 and g["rows"] == len(df) and g["label"] == "Orders"
    log = []
    out = grain.count_order_amounts_once(df, log)
    assert out["Order Total"].sum() == sum(100 + o for o in range(50)) and df["Order Total"].sum() > out["Order Total"].sum()
    assert out["Qty"].sum() == df["Qty"].sum() and out["Line price"].sum() == df["Line price"].sum()          # line-level figures are untouched
    assert "Order ID" in log[0]["detail"] and log[0]["step"] == "Counted order amounts once"
    one = pd.DataFrame({"Order ID": [f"A{i}" for i in range(60)], "Order Total": range(60)})
    assert grain.detect(one) is None and grain.count_order_amounts_once(one, []) is one
    assert grain.key_columns(pd.DataFrame({"Net Settlement": [1, 1, 2, 2, 3, 3] * 5, "Order Date": ["a", "a", "b", "b", "c", "c"] * 5})) == []          # a name that only contains a key word is not a key
    prof = profiler.profile_df(df)
    assert prof["grain"]["orders"] == 50


def test_the_report_names_the_orders_when_a_file_has_several_rows_per_order():
    from app import mainview
    state = {"goal": "", "plan": {}, "key_metrics": [{"label": "Total Order Total", "value": "9,000"}], "profile": {"rows": 640, "cols": 5, "grain": {"label": "Orders", "orders": 320, "rows": 640, "per_order": 2.0}}}
    tiles = mainview.tiles(state, {})
    assert ("Orders", "320") in tiles and ("Rows", "640") in tiles


def test_the_loader_notes_reach_the_cleaning_log(results):
    log = " ".join(" ".join(r.get("cleaning_log") or []) for r in results.values())
    for step in ("Skipped title rows", "Removed totals rows", "Removed a summary row", "Left out a sheet of notes", "Left out empty columns", "Dates written in two orders"):
        assert step in log, step


def test_questions_show_underscore_names_as_words():
    from app import planner
    g = np.random.RandomState(3)
    df = pd.DataFrame({"return_reason": ["size", "damaged", "late"] * 20, "product_line": list("abcd") * 15, "net_revenue": np.round(g.gamma(3, 100, 60), 2), "order_date": pd.date_range("2024-01-01", periods=60)})
    prof = profiler.profile_df(df)
    plan = planner.heuristic_plan(df, prof, "", {}, explore=True)
    assert any("net revenue" in q for q in plan["questions"]) and not any("_" in q for q in plan["questions"]), plan["questions"]


# ------------------------------------------------------------------ order-line files (the Shopify-style fixture)
@pytest.fixture(scope="module")
def shopify_run():
    import os, tempfile
    os.environ["LLM_PROVIDER"] = "none"
    tmp = tempfile.mkdtemp()
    os.environ.update(RUNS_DIR=tmp + "/runs", AI_CACHE_DIR=tmp + "/c", CHART_DPI="50", GLOSSARY_PATH=tmp + "/g.json")
    from app import runner
    f = FIX / "a_shopify_orders.csv"
    rid = runner.create_run("", [(f.name, f.read_bytes())])
    runner._execute(rid)
    wd = runner.run_dir(rid)
    if (runner.read_status(rid) or {}).get("status") == "needs_input":
        qs = json.loads((wd / "questions.json").read_text(encoding="utf-8"))["questions"]
        (wd / "answers.json").write_text(json.dumps({"answers": {q["id"]: q["default"] for q in qs}, "skip": False, "notes": ""}))
        runner._execute(rid)
    return wd, json.loads((wd / "result.json").read_text())


def test_the_order_key_is_found_from_how_values_repeat_not_from_its_name(shopify_run):
    wd, result = shopify_run
    g = result["profile"]["grain"]
    assert g["key"] == "Name" and g["orders"] == 320 and g["rows"] == 640 and g["per_order"] == 2.0 and g["label"] == "Orders"
    assert "Lineitem quantity" in g["line_columns"] and "Email" in g["order_columns"]
    renamed = pd.read_csv(FIX / "a_shopify_orders.csv").rename(columns={"Name": "Ref", "Lineitem quantity": "Units", "Lineitem price": "Each", "Lineitem name": "Item", "Lineitem sku": "Code"})
    g2 = grain.detect(renamed, {})
    assert g2 and g2["key"] == "Ref" and g2["orders"] == 320
    customers = pd.DataFrame({"Customer": [f"C{i % 40}" for i in range(200)], "Date": pd.date_range("2024-01-01", periods=200), "Amount": range(200), "Item": list("abcde") * 40})
    assert grain.detect(customers, {"Date": "date", "Amount": "numeric"}) is None          # a customer's rows differ in date and amount: it is not an order


def test_the_report_shows_the_distinct_order_count_as_a_tile(shopify_run):
    wd, result = shopify_run
    tiles = {t["label"]: t["value"] for t in result["report_view"]["page"]["tiles"]}
    assert tiles["Orders"] == "320"


def test_products_are_ranked_by_line_value_and_match_pandas(shopify_run):
    wd, result = shopify_run
    raw = pd.read_csv(FIX / "a_shopify_orders.csv")
    want = (raw["Lineitem quantity"] * raw["Lineitem price"]).groupby(raw["Lineitem name"]).sum().sort_values(ascending=False)
    clean = pd.read_pickle(wd / "clean.pkl")
    assert len(clean) == len(raw)                                        # the same product twice in one order is two lines: none was dropped as a duplicate
    got = clean.groupby("Lineitem name")["revenue"].sum().sort_values(ascending=False)
    assert got.round(2).to_dict() == want.round(2).to_dict() and list(got.index) == list(want.index)
    rank = next(r for r in result["summary_facts"]["rankings"] if r["group"] == "Lineitem name" and r["measure"] == "revenue" and r["basis"] == "total" and not r.get("kind"))
    assert [b[0] for b in rank["bars"]] == list(want.index) and [round(b[1]) for b in rank["bars"]] == [round(v) for v in want.values]
    assert rank["top"] == want.index[0] and rank["bottom"] == want.index[-1]
    assert not any(r["group"] == "Lineitem name" and r["measure"] in ("Subtotal", "Taxes", "Total") for r in result["summary_facts"]["rankings"])          # an order's own amounts are not split by product


def test_the_line_amount_is_used_when_the_file_has_one():
    from app import retail_roles
    n = 120
    g = np.random.RandomState(2)
    df = pd.DataFrame({"order": [f"O{i // 2}" for i in range(n)], "date": pd.date_range("2024-01-01", periods=n // 2).repeat(2), "paid": np.repeat(g.gamma(3, 100, n // 2).round(2), 2), "qty": g.randint(1, 5, n),
                       "each": g.choice([10.0, 20.0, 35.0, 50.0, 80.0, 120.0], n)})
    df["line_total"] = df["qty"] * df["each"]
    cols = {c["name"]: c for c in profiler.profile_df(df)["columns"]}
    assert retail_roles.derive_product_measure(df, cols) is None          # a column already holds quantity x price: it is the line value
