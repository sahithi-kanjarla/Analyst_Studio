"""Builds the review pack: 26 small datasets for a real-AI review of Analyst Studio, each with an
"expected answers" file computed from pandas so a run can be marked right or wrong without guessing.

    python scripts/make_review_datasets.py              # writes sample_data/review/

Deterministic (seeded), no network, no AI. Every name, phone number and e-mail is made up; no real
personal data. Each dataset is under 3,000 rows and lives in its own folder:

    sample_data/review/R01_bakery_clean_sales/
        bakery_sales.csv
        expected.json          <- row counts, totals, top 3, distinct keys, missing, duplicates, joins

`expected.json` records, per file, how to read it (`reader` + `options`) so a checker can reload it,
and the figures a correct run must reproduce. Where a file is dirty, the figures are computed on the
*cleaned* table and `cleaning_applied` says exactly what cleaning was assumed. Where a file is
genuinely ambiguous (R07), there is no single right number: `ambiguities` lists each reading and the
number it produces, and the app is expected to ask rather than pick one silently.

Style follows scripts/make_varied_datasets.py and reuses its helpers (seeded rng, made-up people,
Indian-format amounts)."""
from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path
from typing import Any, Callable

import numpy as np
import openpyxl
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "sample_data" / "review"
SEED = 20261103
MAX_ROWS = 3000

FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Sana", "Vihaan", "Isha", "Arjun", "Nisha", "Dev", "Kiara", "Rahul", "Anaya", "Imran", "Pooja", "Karan", "Tara", "Yash", "Zoya",
         "Lakshmi", "Faizan", "Ritu", "Omkar", "Sneha", "Bilal", "Gauri", "Nitin", "Asha", "Vikram"]
LAST = ["Sharma", "Verma", "Iyer", "Khan", "Patel", "Reddy", "Nair", "Singh", "Das", "Mehta", "Joshi", "Rao", "Gupta", "Bose", "Menon", "Chavan", "Pillai", "Ansari", "Kulkarni", "Shetty"]
CITIES = ["Mumbai", "Pune", "Delhi", "Bengaluru", "Chennai", "Kolkata", "Hyderabad", "Jaipur", "Ahmedabad", "Indore", "Nagpur", "Kochi"]
AREAS = ["Kothrud", "Aundh", "Baner", "Hadapsar", "Viman Nagar", "Shivaji Nagar", "Kharadi", "Warje"]

# ------------------------------------------------------------------ what each business sells: (item, price, cost, category, code)
CATALOGUE: dict[str, list[tuple[str, float, float, str, str]]] = {
    "kirana store": [("Atta 5kg", 250, 212, "Staples", "KIR-ATT5"), ("Sugar 1kg", 48, 42, "Staples", "KIR-SUG1"), ("Toor Dal 1kg", 165, 140, "Staples", "KIR-TOO1"),
                     ("Refined Oil 1L", 142, 126, "Staples", "KIR-OIL1"), ("Tea 250g", 135, 110, "Beverages", "KIR-TEA2"), ("Rice 10kg", 620, 540, "Staples", "KIR-RIC10"),
                     ("Salt 1kg", 22, 18, "Staples", "KIR-SAL1"), ("Detergent 1kg", 110, 92, "Household", "KIR-DET1"), ("Biscuits Pack", 35, 27, "Snacks", "KIR-BIS1"),
                     ("Milk Powder 500g", 290, 254, "Dairy", "KIR-MLK5"), ("Soap Bar", 42, 33, "Household", "KIR-SOA1"), ("Poha 1kg", 58, 48, "Staples", "KIR-POH1")],
    "bakery": [("Chocolate Cake 1kg", 650, 410, "Cakes", "BAK-CHO1"), ("Vanilla Cupcake", 60, 34, "Cakes", "BAK-CUP1"), ("Brown Bread", 45, 28, "Breads", "BAK-BRD1"),
               ("Croissant", 85, 49, "Breads", "BAK-CRO1"), ("Veg Puff", 30, 17, "Savouries", "BAK-PUF1"), ("Butter Cookies 250g", 180, 108, "Cookies", "BAK-COO2"),
               ("Birthday Cake 2kg", 1450, 920, "Cakes", "BAK-BIR2"), ("Doughnut", 70, 41, "Cakes", "BAK-DOU1"), ("Rusk 300g", 55, 34, "Breads", "BAK-RUS3"),
               ("Walnut Brownie", 95, 56, "Cookies", "BAK-BRW1"), ("Cheese Garlic Bread", 120, 71, "Breads", "BAK-CGB1"), ("Pineapple Pastry", 75, 44, "Cakes", "BAK-PIN1")],
    "clothing boutique": [("Cotton Kurta", 899, 520, "Ethnic", "BTQ-KUR1"), ("Anarkali Suit", 2450, 1480, "Ethnic", "BTQ-ANA1"), ("Cotton Saree", 1850, 1150, "Sarees", "BTQ-SAR1"),
                          ("Palazzo Pants", 640, 355, "Bottoms", "BTQ-PAL1"), ("Printed Dupatta", 420, 230, "Accessories", "BTQ-DUP1"), ("Designer Blouse", 780, 430, "Sarees", "BTQ-BLO1"),
                          ("Bridal Lehenga", 8900, 5600, "Ethnic", "BTQ-LEH1"), ("Denim Jeans", 1290, 760, "Bottoms", "BTQ-JEA1"), ("Rayon Top", 560, 305, "Western", "BTQ-TOP1"),
                          ("Knit Shrug", 690, 390, "Western", "BTQ-SHR1"), ("Silk Stole", 540, 300, "Accessories", "BTQ-STO1"), ("Chikan Kurti", 1150, 690, "Ethnic", "BTQ-CHI1")],
    "electronics seller": [("Bluetooth Speaker", 1799, 1180, "Audio", "ELC-SPK1"), ("Power Bank 10000mAh", 1299, 870, "Power", "ELC-PWB1"), ("Wireless Earbuds", 2499, 1620, "Audio", "ELC-EAR1"),
                           ("Phone Case", 299, 118, "Accessories", "ELC-CAS1"), ("USB-C Cable 1m", 249, 96, "Cables", "ELC-USB1"), ("Smart Watch", 3499, 2310, "Wearables", "ELC-WAT1"),
                           ("Wireless Mouse", 699, 430, "Computer", "ELC-MOU1"), ("Mechanical Keyboard", 2899, 1950, "Computer", "ELC-KEY1"), ("Charger 20W", 899, 560, "Power", "ELC-CHG1"),
                           ("HDMI Cable 2m", 399, 180, "Cables", "ELC-HDM1"), ("Laptop Sleeve", 749, 405, "Accessories", "ELC-SLV1"), ("Neckband Earphone", 1099, 690, "Audio", "ELC-NCK1")],
    "home-based food seller": [("Mango Pickle 500g", 280, 165, "Pickles", "HFD-MNG5"), ("Besan Laddu 500g", 340, 205, "Sweets", "HFD-LAD5"), ("Masala Powder 100g", 120, 68, "Masala", "HFD-MAS1"),
                               ("Pure Ghee 500ml", 520, 395, "Dairy", "HFD-GHE5"), ("Urad Papad 200g", 95, 52, "Snacks", "HFD-PAP2"), ("Chakli 250g", 150, 86, "Snacks", "HFD-CHK2"),
                               ("Rice Murukku 250g", 145, 84, "Snacks", "HFD-MUR2"), ("Dry Fruit Chikki", 260, 158, "Sweets", "HFD-CHI1"), ("Tomato Chutney 300g", 190, 112, "Pickles", "HFD-TOM3"),
                               ("Idli Batter 1kg", 85, 48, "Batter", "HFD-IDL1"), ("Garlic Chutney 200g", 165, 95, "Pickles", "HFD-GAR2"), ("Methi Thepla Pack", 130, 74, "Snacks", "HFD-THE1")],
    "handicrafts seller": [("Terracotta Vase", 850, 470, "Pottery", "HCF-VAS1"), ("Jute Tote Bag", 540, 295, "Textile", "HCF-JUT1"), ("Wooden Elephant", 1250, 690, "Woodcraft", "HCF-ELE1"),
                           ("Madhubani Painting", 2400, 1340, "Art", "HCF-MAD1"), ("Brass Diya Set", 690, 380, "Metalcraft", "HCF-DIY1"), ("Bamboo Serving Tray", 780, 420, "Woodcraft", "HCF-TRA1"),
                           ("Clay Pot Set", 620, 340, "Pottery", "HCF-POT1"), ("Block Print Cushion", 450, 240, "Textile", "HCF-CUS1"), ("Beaded Necklace", 390, 195, "Jewellery", "HCF-NEC1"),
                           ("Macrame Wall Hanging", 1150, 620, "Textile", "HCF-MAC1"), ("Dhokra Figurine", 1680, 940, "Metalcraft", "HCF-DHO1"), ("Warli Coasters", 360, 185, "Art", "HCF-COA1")],
    "salon": [("Haircut", 350, 0, "Hair", "SAL-HRC1"), ("Global Hair Colour", 2200, 0, "Hair", "SAL-COL1"), ("Gold Facial", 1450, 0, "Skin", "SAL-FAC1"),
              ("Eyebrow Threading", 60, 0, "Grooming", "SAL-THR1"), ("Manicure", 450, 0, "Nails", "SAL-MAN1"), ("Pedicure", 650, 0, "Nails", "SAL-PED1"),
              ("Bridal Makeup", 8500, 0, "Bridal", "SAL-BRD1"), ("Head Massage", 400, 0, "Hair", "SAL-MAS1"), ("Full Arm Waxing", 550, 0, "Grooming", "SAL-WAX1"),
              ("Hair Spa", 1200, 0, "Hair", "SAL-SPA1"), ("Keratin Treatment", 4500, 0, "Hair", "SAL-KER1"), ("Party Makeup", 2800, 0, "Bridal", "SAL-PRT1")],
}

MISSING_WORDS = {"", "-", "--", "n/a", "n.a.", "na", "none", "nil", "unknown", "not known", "not available", "?", "null", "nan", "#n/a", "missing", "tbd"}


# ------------------------------------------------------------------ small helpers
def rng_for(n: int) -> tuple[random.Random, np.random.RandomState]:
    return random.Random(SEED + n), np.random.RandomState(SEED + n)


def person(r: random.Random) -> tuple[str, str, str]:
    f, l = r.choice(FIRST), r.choice(LAST)
    return f"{f} {l}", f"{f.lower()}.{l.lower()}{r.randint(1, 99)}@example.com", f"{r.choice('6789')}{r.randint(10 ** 8, 10 ** 9 - 1)}"


def indian(n: float) -> str:
    """12500 -> '12,500'; 125000 -> '1,25,000' (the Indian digit grouping)."""
    s = f"{int(round(n))}"
    if len(s) <= 3:
        return s
    head, tail, parts = s[:-3], s[-3:], []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def money(v: Any) -> float:
    """Parses one amount the way a careful person would read it: '₹1,25,000', 'Rs. 12.5k', '1.2 lakh',
    '12,500/-', '(450)' as a negative. Placeholder words and unreadable text become NaN."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return float("nan")
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    s = str(v).strip().lower()
    if s in MISSING_WORDS:
        return float("nan")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("₹", "").replace("inr", "").replace("rs.", "").replace("rs", "").replace("/-", "").replace(",", "").replace(" ", "")
    mult = 1.0
    for suffix, m in (("crore", 1e7), ("cr", 1e7), ("lakh", 1e5), ("lacs", 1e5), ("lac", 1e5), ("k", 1e3)):
        if s.endswith(suffix):
            s, mult = s[: -len(suffix)], m
            break
    try:
        x = float(s)
    except ValueError:
        return float("nan")
    return -(x * mult) if neg else x * mult


def blank_out(series: pd.Series) -> pd.Series:
    """Placeholder words -> NaN, so 'missing' means missing."""
    return series.map(lambda v: float("nan") if isinstance(v, str) and v.strip().lower() in MISSING_WORDS else v)


def freeze_xlsx(path: Path) -> None:
    """Rewrites an .xlsx with a fixed timestamp on every zip entry, so the file is byte-identical on
    every run. Without this, openpyxl stamps each entry with the time it was written."""
    import re
    import zipfile
    with zipfile.ZipFile(path) as z:
        items = [(i.filename, i.external_attr, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, attr, data in items:
            if name == "docProps/core.xml":                                    # openpyxl stamps the save time here
                data = re.sub(rb">[^<]*</dcterms:(created|modified)>", rb">2026-01-01T00:00:00Z</dcterms:\1>", data)
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, attr
            z.writestr(info, data)


def jsonable(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else round(float(o), 4)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (dt.date, dt.datetime, pd.Timestamp)):
        return str(o)
    raise TypeError(f"not JSON-serialisable: {type(o)}")


def num(x: Any) -> float | None:
    """A float ready for JSON: rounded to 2 places, None when it is not a number."""
    if x is None:
        return None
    f = float(x)
    return None if np.isnan(f) else round(f, 2)


# ------------------------------------------------------------------ the framework: write the files, read them back, compute the expected answers
class File:
    """One file of a dataset: how it is written, and how it must be read back."""

    def __init__(self, name: str, fmt: str = "csv", df: pd.DataFrame | None = None, writer: Callable[[Path], None] | None = None,
                 reader: str = "csv", options: dict[str, Any] | None = None, note: str = "") -> None:
        self.name, self.fmt, self.df, self.writer = name, fmt, df, writer
        self.reader, self.options, self.note = reader, options or {}, note

    def write(self, path: Path) -> None:
        if self.writer is not None:
            self.writer(path)
        elif self.fmt == "csv":
            self.df.to_csv(path, index=False, encoding="utf-8")
        elif self.fmt == "csv-bom":
            self.df.to_csv(path, index=False, encoding="utf-8-sig")
        elif self.fmt == "tsv":
            self.df.to_csv(path, index=False, sep="\t", encoding="utf-8")
        elif self.fmt == "xlsx":
            with pd.ExcelWriter(path, engine="openpyxl") as xw:
                xw.book.properties.created = xw.book.properties.modified = dt.datetime(2026, 1, 1)
                self.df.to_excel(xw, index=False, sheet_name="Sheet1")
        else:
            raise ValueError(self.fmt)

    def read(self, path: Path) -> pd.DataFrame:
        if self.reader == "excel":
            return pd.read_excel(path, **self.options)
        return pd.read_csv(path, **self.options)


def spec(**kw: Any) -> dict[str, Any]:
    return kw


def describe(df: pd.DataFrame) -> dict[str, Any]:
    return {"rows": int(len(df)), "columns": int(len(df.columns)), "column_names": [str(c) for c in df.columns],
            "missing_per_column": {str(c): int(df[c].isna().sum()) for c in df.columns},
            "exact_duplicate_rows": int(len(df) - len(df.drop_duplicates()))}


def top_n(df: pd.DataFrame, group: str, value: str, n: int = 3) -> list[dict[str, Any]]:
    d = df[[group, value]].dropna(subset=[group])
    g = d.groupby(d[group].astype(str))[value].sum().sort_values(ascending=False)
    return [{"name": k, "total": num(v)} for k, v in list(g.items())[:n]]


def build(s: dict[str, Any]) -> dict[str, Any]:
    """Writes one dataset, reads every file back with pandas, and computes its expected answers."""
    folder = OUT / s["dir"]
    folder.mkdir(parents=True, exist_ok=True)
    files: list[File] = s["files"]
    for f in files:
        f.write(folder / f.name)

    raw = {f.name: f.read(folder / f.name) for f in files}                   # the check that every file loads
    for name, df in raw.items():
        if len(df) > MAX_ROWS:
            raise AssertionError(f"{s['id']} {name}: {len(df)} rows is over the {MAX_ROWS}-row limit")

    clean: dict[str, pd.DataFrame] = s.get("clean", lambda d: d)({k: v.copy() for k, v in raw.items()})
    out: dict[str, Any] = {
        "id": s["id"], "business_type": s["business"], "kind": s["kind"], "what_it_tests": s["tests"], "app_should": s["app_should"],
        "generated_by": "scripts/make_review_datasets.py", "seed": SEED,
        "files": {f.name: {"reader": f.reader, "options": f.options, "note": f.note, **describe(raw[f.name])} for f in files},
        "cleaning_applied": s.get("cleaning", []),
        "answers": {},
    }
    if any(f.name in clean and not clean[f.name].equals(raw[f.name]) for f in files):
        out["files_after_cleaning"] = {n: describe(df) for n, df in clean.items() if not df.equals(raw[n])}

    a = out["answers"]
    a["row_count"] = {n: int(len(df)) for n, df in clean.items()}
    if s.get("amount"):
        name, col, label = s["amount"]
        a["main_total"] = {"file": name, "column": col, "means": label, "value": num(clean[name][col].sum()),
                           "rows_counted": int(clean[name][col].notna().sum()), "mean": num(clean[name][col].mean())}
    if s.get("category"):
        name, col, val = s["category"]
        a["top_3_main_category"] = {"file": name, "group_by": col, "ranked_by": val, "items": top_n(clean[name], col, val)}
    for extra in s.get("extra_tops", []):
        name, col, val, key = extra
        a[key] = {"file": name, "group_by": col, "ranked_by": val, "items": top_n(clean[name], col, val)}
    if s.get("keys"):
        a["distinct_key_counts"] = {f"{n}.{c}": int(clean[n][c].nunique(dropna=True)) for n, cols in s["keys"].items() for c in cols}
    for name, col, key, label in s.get("extra_sums", []):
        a[key] = {"file": name, "column": col, "means": label, "value": num(clean[name][col].sum())}
    a["duplicates"] = {n: {"exact_duplicate_rows": int(len(df) - len(df.drop_duplicates()))} for n, df in raw.items()}
    for name, cols in s.get("key_duplicates", {}).items():
        for c in cols:
            d = raw[name][c]
            a["duplicates"].setdefault(name, {})[f"repeated_{c}_values"] = sorted(str(v) for v in d[d.duplicated()].dropna().unique())
    a["missing_values_per_column"] = {n: describe(df)["missing_per_column"] for n, df in clean.items()}
    if s.get("joins"):
        a["joins"] = [_join(clean, j) for j in s["joins"]]
    if s.get("ambiguities"):
        out["ambiguities"] = s["ambiguities"](clean)
    for k, v in s.get("also", {}).items():
        a[k] = v(clean) if callable(v) else v

    (folder / "expected.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=jsonable), encoding="utf-8")
    return out


def _join(clean: dict[str, pd.DataFrame], j: dict[str, Any]) -> dict[str, Any]:
    left, right, on = clean[j["left"]], clean[j["right"]], j["on"]
    lk, rk = j.get("left_on", on), j.get("right_on", on)
    inner = left.merge(right, left_on=lk, right_on=rk, how="inner")
    lvals, rvals = set(left[lk].dropna().unique()), set(right[rk].dropna().unique())
    orphan_l, orphan_r = sorted(str(v) for v in lvals - rvals), sorted(str(v) for v in rvals - lvals)
    dup_r = sorted(str(v) for v in right[rk][right[rk].duplicated()].dropna().unique())
    return {"left": j["left"], "right": j["right"], "on": f"{lk} = {rk}", "left_rows": int(len(left)), "right_rows": int(len(right)),
            "inner_join_rows": int(len(inner)), "left_join_rows": int(len(left.merge(right, left_on=lk, right_on=rk, how="left"))),
            "left_rows_with_no_match": int(left[lk].isin(rvals).eq(False).sum()), "unmatched_ids_in_left": orphan_l,
            "unmatched_ids_in_right": orphan_r, "repeated_key_values_in_right": dup_r,
            "note": j.get("note", "")}


def parse_date_any(v: Any) -> dt.date | None:
    """Reads one date in any of the formats this pack uses. Only used where the date is unambiguous."""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d %b %Y", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(str(v).strip(), fmt).date()

        except ValueError:
            continue
    return None


# ================================================================== sales and orders (R01-R09)
def r01() -> dict[str, Any]:
    r, _ = rng_for(11)
    cat = CATALOGUE["bakery"]
    rows = []
    for n in range(1, 901):
        item, price, cost, group, code = r.choice(cat)
        q = r.choice([1, 1, 1, 2, 2, 3, 4, 6])
        d = dt.date(2026, 1, 5) + dt.timedelta(days=r.randint(0, 269))
        rows.append({"order_id": f"B{n:05d}", "order_date": d.isoformat(), "item_code": code, "item": item, "category": group, "quantity": q,
                     "unit_price": price, "unit_cost": cost, "amount": round(price * q, 2), "cost_amount": round(cost * q, 2),
                     "payment_mode": r.choice(["UPI", "Cash", "Card", "UPI", "UPI"]), "customer_type": r.choice(["Walk-in", "Walk-in", "Regular", "Online"]),
                     "outlet": r.choice(["FC Road", "Baner"])})
    df = pd.DataFrame(rows)
    f = "bakery_sales.csv"
    return spec(id="R01", dir="R01_bakery_clean_sales", business="bakery", kind="sales",
                tests="a clean sales file that already carries a cost column, so margin needs no guessing",
                app_should="ask few or no clarifying questions, and report profit and margin as well as revenue",
                files=[File(f, df=df, note="clean: no missing values, no duplicates, one date format, cost column present")],
                amount=(f, "amount", "revenue = quantity x unit_price"), category=(f, "category", "amount"),
                keys={f: ["order_id", "item_code"]},
                extra_sums=[(f, "cost_amount", "total_cost", "cost of goods sold = quantity x unit_cost")],
                also={"gross_profit": lambda c: num(c[f]["amount"].sum() - c[f]["cost_amount"].sum()),
                      "gross_margin_pct": lambda c: num(100 * (1 - c[f]["cost_amount"].sum() / c[f]["amount"].sum())),
                      "date_range": lambda c: [str(c[f]["order_date"].min()), str(c[f]["order_date"].max())]})


def r02() -> dict[str, Any]:
    r, _ = rng_for(12)
    cat = CATALOGUE["salon"]
    rows = []
    for n in range(1, 821):
        item, price, _, group, code = r.choice(cat)
        d = dt.date(2026, 2, 1) + dt.timedelta(days=r.randint(0, 240))
        disc = r.choice([0, 0, 0, 0, 50, 100, 200])
        rows.append({"invoice_no": f"SAL-{n:04d}", "service_date": d.isoformat(), "service_code": code, "service": item, "service_group": group,
                     "stylist": r.choice(["Ramesh", "Sunita", "Gopal", "Farida", "Nikhil"]), "client_type": r.choice(["New", "Repeat", "Repeat", "Repeat", "Member"]),
                     "duration_min": r.choice([15, 30, 45, 60, 90, 120]), "amount": round(price - disc, 2), "discount": disc,
                     "payment_mode": r.choice(["UPI", "Cash", "Card", "UPI"]), "branch": r.choice(["Kothrud", "Aundh"])})
    df = pd.DataFrame(rows)
    f = "salon_services.csv"
    return spec(id="R02", dir="R02_salon_sales_no_cost", business="salon", kind="sales",
                tests="a clean sales file with no cost column anywhere",
                app_should="report revenue and never invent a profit or margin figure; it may say cost data is missing",
                files=[File(f, df=df, note="clean, but no cost or purchase-price column exists")],
                amount=(f, "amount", "revenue after discount"), category=(f, "service_group", "amount"),
                keys={f: ["invoice_no", "service_code", "stylist"]},
                extra_sums=[(f, "discount", "total_discount", "discount given")],
                extra_tops=[(f, "stylist", "amount", "top_3_stylists_by_amount")],
                also={"cost_columns_present": [], "profit_is_computable": False})


def r03() -> dict[str, Any]:
    r, g = rng_for(13)
    cat = CATALOGUE["kirana store"]
    rows = []
    for n in range(1, 941):
        item, price, cost, group, code = r.choice(cat)
        q = r.choice([1, 1, 2, 2, 3, 5, 10])
        d = dt.date(2026, 3, 1) + dt.timedelta(days=r.randint(0, 200))
        amt = round(price * q, 2)
        rows.append({"bill_no": 20000 + n, "bill_date": d, "item": item, "item_code": code, "category": group, "qty": q,
                     "rate": price, "amount": amt, "payment_mode": r.choice(["Cash", "UPI", "Card", "Credit"]),
                     "offer_code": "", "store_code": "KIR-01", "entered_by": r.choice(["Ramesh", "Sunita"])})
    # --- the defects, each one deliberate -------------------------------------------------
    for i, row in enumerate(rows):
        d: dt.date = row["bill_date"]
        day = max(13, d.day)                                                  # every day is 13+ so the mixed date formats stay unambiguous
        d = d.replace(day=min(day, 28))
        row["bill_date"] = [d.strftime("%d/%m/%Y"), d.strftime("%Y-%m-%d"), d.strftime("%d-%b-%Y"), d.strftime("%d-%m-%Y")][i % 4]
        if i % 37 == 0:
            row["amount"] = "₹" + indian(row["amount"])                       # a currency symbol and Indian grouping
        elif i % 53 == 0:
            row["amount"] = f"Rs. {row['amount'] / 1000:g}k"                  # a text amount
        if i % 29 == 0:
            row["offer_code"] = r.choice(["DIWALI5", "REG10"])                 # the rest stay blank, which means "no offer"
    for i in (120, 240, 360, 480):                                            # four outliers: a wholesale bill 60-90x the usual
        rows[i]["amount"] = round(money(rows[i]["amount"]) * r.choice([60, 75, 90]), 2)
    for i in range(0, 940, 38):
        rows[i]["qty"] = -1                                                   # -1 is this shop's placeholder for "not recorded"
    for i in range(3, 940, 24):
        rows[i]["payment_mode"] = r.choice(["N/A", "unknown", "", "-"])        # placeholder words for a missing mode
    for i in range(7, 240, 8):                                                # 30 return lines, written two different ways
        rows[i]["amount"] = f"({money(rows[i]['amount']):.2f})" if i % 16 == 7 else -abs(money(rows[i]["amount"]))
        rows[i]["item"] = rows[i]["item"] + " (return)"
    df = pd.DataFrame(rows + [dict(rows[i]) for i in range(5, 405, 10)])       # 40 exact duplicate rows appended at the end
    f = "kirana_sales_messy.csv"

    def clean(c: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        df = c[f].drop_duplicates()
        df["amount_parsed"] = df["amount"].map(money)
        df["qty_clean"] = df["qty"].map(lambda v: float("nan") if v == -1 else v)
        df["payment_mode"] = blank_out(df["payment_mode"])
        df["offer_code"] = df["offer_code"].fillna("none")
        df["bill_date_parsed"] = df["bill_date"].map(parse_date_any)
        return {f: df.reset_index(drop=True)}

    return spec(id="R03", dir="R03_kirana_sales_defects", business="kirana store", kind="sales",
                tests="duplicates, blanks that mean none, placeholder values (-1, N/A, unknown, -), outliers, a constant column, four date formats, text amounts, currency symbols and negative returns",
                app_should="log every cleaning action, drop the 40 exact duplicates, read the text amounts, treat -1 and the placeholder words as missing, keep the negative returns as returns, and flag the constant column",
                files=[File(f, df=df, note="40 exact duplicate rows at the end; amount mixes plain numbers, '₹1,25,000', 'Rs. 1.2k' and bracketed negatives")],
                clean=clean,
                cleaning=["dropped 40 exact duplicate rows", "parsed amount text into numbers (₹ symbol, Indian grouping, 'Rs. 1.2k', brackets = negative)",
                          "qty of -1 treated as missing (qty_clean)", "payment_mode placeholder words (N/A, unknown, -, blank) treated as missing",
                          "blank offer_code read as 'none', not missing", "bill_date parsed from its four formats (every day is 13+, so no day/month ambiguity)"],
                amount=(f, "amount_parsed", "revenue, returns counted as negative"), category=(f, "category", "amount_parsed"),
                keys={f: ["bill_no", "item_code", "payment_mode"]},
                also={"constant_columns": ["store_code"],
                      "return_lines": lambda c: int((c[f]["amount_parsed"] < 0).sum()),
                      "total_excluding_returns": lambda c: num(c[f].loc[c[f]["amount_parsed"] > 0, "amount_parsed"].sum()),
                      "total_of_returns": lambda c: num(c[f].loc[c[f]["amount_parsed"] < 0, "amount_parsed"].sum()),
                      "rows_with_placeholder_qty": lambda c: int(c[f]["qty_clean"].isna().sum()),
                      "rows_with_missing_payment_mode": lambda c: int(c[f]["payment_mode"].isna().sum()),
                      "four_largest_amounts": lambda c: [num(v) for v in c[f]["amount_parsed"].nlargest(4)],
                      "total_if_outliers_excluded": lambda c: num(c[f]["amount_parsed"].drop(c[f]["amount_parsed"].nlargest(4).index).sum())})


def r04() -> dict[str, Any]:
    r, g = rng_for(14)
    cat = CATALOGUE["electronics seller"]
    n = 700
    picks = [r.choice(cat) for _ in range(n)]
    qty = [r.choice([1, 1, 1, 2, 3]) for _ in range(n)]
    amount = [round(p[1] * q, 2) for p, q in zip(picks, qty)]
    d: dict[str, Any] = {"order_id": [f"EO{i:05d}" for i in range(1, n + 1)],
                         "order_date": [(dt.date(2026, 1, 1) + dt.timedelta(days=int(x))).isoformat() for x in g.randint(0, 250, n)],
                         "sku": [p[4] for p in picks], "item": [p[0] for p in picks], "category": [p[3] for p in picks],
                         "channel": [r.choice(["Website", "Marketplace", "WhatsApp", "Retail"]) for _ in range(n)],
                         "quantity": qty, "unit_price": [p[1] for p in picks], "amount": amount,
                         "cost_amount": [round(p[2] * q, 2) for p, q in zip(picks, qty)]}
    d["amount_inr"] = list(amount)                                             # an exact copy of amount
    d["total_amount"] = [round(a) for a in amount]                             # amount rounded: nearly the same column
    d["qty"] = list(qty)
    d["sku_code"] = list(d["sku"])
    d["item_name"] = list(d["item"])
    d["order_date_copy"] = list(d["order_date"])
    for k in range(1, 19):
        d[f"attr_{k:02d}"] = [r.choice(["A", "B", "C", "D", "E"][: 2 + k % 4]) for _ in range(n)]
    for k in range(1, 17):
        d[f"metric_{k:02d}"] = np.round(g.gamma(2 + k % 4, 30 + k, n), 2)
    for k in range(1, 13):
        d[f"flag_{k:02d}"] = g.randint(0, 2, n)
    d["notes"] = [r.choice(["", "", "", "gift wrap", "call before delivery"]) for _ in range(n)]
    d["internal_ref"] = [f"REF-{g.randint(10 ** 6, 10 ** 7)}" for _ in range(n)]
    d["sync_status"] = ["synced"] * n
    d["legacy_id"] = [None] * n
    d["batch_tag"] = [r.choice(["b1", "b2", "b3"]) for _ in range(n)]
    d["reserved_1"] = [None] * n
    df = pd.DataFrame(d)
    f = "electronics_wide.csv"
    return spec(id="R04", dir="R04_electronics_wide", business="electronics seller", kind="sales",
                tests=f"a wide file: {len(df.columns)} columns, of which about nine matter and the rest are noise, near-duplicates or empty",
                app_should="find the few columns that matter (order_date, amount, category, quantity, cost_amount), say which columns are copies of each other, and not drown the report in noise",
                files=[File(f, df=df, note="amount_inr is an exact copy of amount; total_amount is amount rounded; qty/sku_code/item_name/order_date_copy are copies; legacy_id and reserved_1 are empty; sync_status is constant")],
                amount=(f, "amount", "revenue"), category=(f, "category", "amount"), keys={f: ["order_id", "sku"]},
                extra_sums=[(f, "cost_amount", "total_cost", "cost of goods sold")],
                also={"column_count": lambda c: int(len(c[f].columns)),
                      "columns_that_matter": ["order_date", "category", "channel", "quantity", "unit_price", "amount", "cost_amount", "sku", "item"],
                      "duplicate_or_copy_columns": {"amount_inr": "exact copy of amount", "total_amount": "amount rounded to whole rupees", "qty": "copy of quantity",
                                                    "sku_code": "copy of sku", "item_name": "copy of item", "order_date_copy": "copy of order_date"},
                      "empty_columns": ["legacy_id", "reserved_1"], "constant_columns": ["sync_status"],
                      "gross_profit": lambda c: num(c[f]["amount"].sum() - c[f]["cost_amount"].sum())})


def r05() -> dict[str, Any]:
    r, _ = rng_for(15)
    cat = CATALOGUE["home-based food seller"]
    rows = []
    for n in range(1, 621):
        item, price, _, _, _ = r.choice(cat)
        q = r.choice([1, 1, 2, 2, 3, 4])
        rows.append({"date": (dt.date(2026, 4, 1) + dt.timedelta(days=r.randint(0, 180))).isoformat(), "item": item, "quantity": q,
                     "amount": round(price * q, 2), "area": r.choice(AREAS)})
    df = pd.DataFrame(rows)
    f = "home_food_sales.csv"
    return spec(id="R05", dir="R05_home_food_narrow", business="home-based food seller", kind="sales",
                tests="a narrow file: five columns and nothing else, no ids, no cost, no customer",
                app_should="still produce a useful report from five columns, and not claim findings the columns cannot support (no customer, no margin, no order id)",
                files=[File(f, df=df, note="five columns only; item is the only category-like column")],
                amount=(f, "amount", "revenue"), category=(f, "item", "amount"), keys={f: ["item", "area", "date"]},
                extra_tops=[(f, "area", "amount", "top_3_areas_by_amount")],
                also={"column_count": 5, "has_order_id": False, "has_cost": False, "has_customer": False,
                      "total_quantity": lambda c: int(c[f]["quantity"].sum())})


def r06() -> dict[str, Any]:
    r, _ = rng_for(16)
    cat = CATALOGUE["handicrafts seller"]
    rows = []
    for n in range(1, 461):
        oid = f"HC-{4000 + n}"
        d = (dt.date(2026, 2, 10) + dt.timedelta(days=r.randint(0, 210))).isoformat()
        name, email, _ = person(r)
        lines = [(r.choice(cat), r.choice([1, 1, 1, 2, 3])) for _ in range(r.choice([1, 1, 2, 2, 3, 4]))]
        order_total = round(sum(p[1] * q for p, q in lines), 2)
        ship = 0.0 if order_total > 2000 else 90.0
        for k, (p, q) in enumerate(lines, start=1):
            rows.append({"order_id": oid, "order_date": d, "customer_name": name, "customer_email": email,
                         "order_amount": order_total, "order_shipping": ship, "order_payment_mode": r.choice(["UPI", "Card", "COD"]),
                         "line_no": k, "product": p[0], "product_code": p[4], "craft_type": p[3], "line_qty": q,
                         "line_unit_price": p[1], "line_amount": round(p[1] * q, 2)})
    df = pd.DataFrame(rows)
    f = "handicraft_order_lines.csv"
    return spec(id="R06", dir="R06_handicrafts_order_lines", business="handicrafts seller", kind="orders",
                tests="long format: one row per order line, order ids repeating, and the order-level amount repeated on every line of that order",
                app_should="count orders by distinct order_id and not add order_amount up once per line — the naive sum is far too high",
                files=[File(f, df=df, note="order_amount, order_shipping and order_payment_mode repeat on every line of the same order")],
                amount=(f, "line_amount", "revenue summed from the line amounts (the correct way)"),
                category=(f, "craft_type", "line_amount"), keys={f: ["order_id", "product_code", "customer_email"]},
                also={"line_rows": lambda c: int(len(c[f])),
                      "distinct_orders": lambda c: int(c[f]["order_id"].nunique()),
                      "order_amount_summed_per_distinct_order": lambda c: num(c[f].drop_duplicates("order_id")["order_amount"].sum()),
                      "order_amount_summed_naively_over_all_rows": lambda c: num(c[f]["order_amount"].sum()),
                      "shipping_per_distinct_order": lambda c: num(c[f].drop_duplicates("order_id")["order_shipping"].sum()),
                      "average_lines_per_order": lambda c: num(len(c[f]) / c[f]["order_id"].nunique()),
                      "note": "line_amount summed over all rows equals order_amount summed over distinct orders; the naive order_amount sum is the trap"})


def r07() -> dict[str, Any]:
    r, _ = rng_for(17)
    cat = CATALOGUE["clothing boutique"]
    rows = []
    for n in range(1, 701):
        item, price, _, group, code = r.choice(cat)
        q = r.choice([1, 1, 1, 2])
        gross = round(price * q, 2)
        disc = round(gross * r.choice([0, 0, 0.05, 0.1, 0.15]), 2)
        fee = round((gross - disc) * 0.03, 2)
        status = r.choices(["Completed", "Cancelled", "Returned", "Refunded", "Partially Refunded"], [74, 8, 8, 6, 4])[0]
        d = dt.date(2026, r.randint(1, 12), r.randint(1, 12))                  # day and month both 1-12: the order cannot be told from the value
        rows.append({"bill_no": f"BQ{n:04d}", "bill_date": d.strftime("%d/%m/%Y"), "item": item, "item_code": code, "category": group,
                     "qty": q, "revenue": gross, "net_received": round(gross - disc - fee, 2), "discount_amount": disc,
                     "status": status, "sales_girl": r.choice(["Meera", "Tara", "Zoya"]), "note": ""})
    for i in range(0, 700, 15):
        rows[i]["net_received"] = 0                                            # 0 here could mean "nothing came in" or "nobody filled it in"
    df = pd.DataFrame(rows)
    f = "boutique_bills.csv"

    def ambiguities(c: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        df = c[f]
        day_first = pd.to_datetime(df["bill_date"], format="%d/%m/%Y")
        month_first = pd.to_datetime(df["bill_date"], format="%m/%d/%Y")
        m1 = df.assign(m=day_first.dt.strftime("%Y-%m")).groupby("m")["revenue"].sum().sort_values(ascending=False)
        m2 = df.assign(m=month_first.dt.strftime("%Y-%m")).groupby("m")["revenue"].sum().sort_values(ascending=False)
        done = df["status"].eq("Completed")
        nonzero = df["net_received"] != 0
        return [
            {"question": "Is bill_date day/month or month/day? Every value has both parts between 1 and 12, so the file cannot say.",
             "readings": [{"reading": "day first (dd/mm/yyyy)", "busiest_month": m1.index[0], "busiest_month_revenue": num(m1.iloc[0])},
                          {"reading": "month first (mm/dd/yyyy)", "busiest_month": m2.index[0], "busiest_month_revenue": num(m2.iloc[0])}]},
            {"question": "Which column is revenue: revenue (before discount and fee) or net_received (after)?",
             "readings": [{"reading": "revenue", "total": num(df["revenue"].sum())},
                          {"reading": "net_received", "total": num(df["net_received"].sum())}]},
            {"question": "Should Cancelled, Returned, Refunded and Partially Refunded bills count towards sales?",
             "readings": [{"reading": "count every row", "total_revenue": num(df["revenue"].sum()), "rows": int(len(df))},
                          {"reading": "count only Completed", "total_revenue": num(df.loc[done, "revenue"].sum()), "rows": int(done.sum())}]},
            {"question": "Do the zeros in net_received mean nothing was received, or that the field was left blank?",
             "readings": [{"reading": "zero means nothing was received", "mean_net_received": num(df["net_received"].mean()), "rows_counted": int(len(df))},
                          {"reading": "zero means missing, so leave those rows out", "mean_net_received": num(df.loc[nonzero, "net_received"].mean()), "rows_counted": int(nonzero.sum())}]},
        ]

    return spec(id="R07", dir="R07_boutique_ambiguous", business="clothing boutique", kind="sales",
                tests="four ambiguities that each change a headline number: an unresolvable day/month order, two columns that could both be revenue, a status column mixing cancelled/returned/refunded, and zeros that could be none or missing",
                app_should="ask clarifying questions here rather than pick a reading silently; whichever reading it takes, the figure must match this file's reading for it",
                files=[File(f, df=df, note="bill_date has day and month both between 1 and 12 on every row; net_received is 0 on 47 rows")],
                amount=(f, "revenue", "the gross revenue reading (one of two defensible readings - see ambiguities)"),
                category=(f, "category", "revenue"), keys={f: ["bill_no", "item_code", "status"]},
                extra_sums=[(f, "net_received", "net_received_total", "the net reading of revenue"),
                            (f, "discount_amount", "total_discount", "discount given")],
                ambiguities=ambiguities,
                also={"status_counts": lambda c: {k: int(v) for k, v in c[f]["status"].value_counts().items()},
                      "rows_with_zero_net_received": lambda c: int((c[f]["net_received"] == 0).sum())})


def r08() -> dict[str, Any]:
    r, _ = rng_for(18)
    cat = CATALOGUE["kirana store"]
    data, totals = [], {"qty": 0, "amount": 0.0}
    for n in range(1, 521):
        item, price, _, _, _ = r.choice(cat)
        q = r.randint(1, 12)
        amt = round(price * q, 2)
        totals["qty"] += q
        totals["amount"] += amt
        data.append([20000 + n, (dt.date(2026, 4, 1) + dt.timedelta(days=r.randint(0, 180))).isoformat(), person(r)[0], item, q, price, amt])
    head = ["Bill No", "Date", "Customer", "Item", "Quantity", "Rate", "Amount"]

    def writer(path: Path) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Daily Sales"
        ws.append(["Shri Ganesh Kirana Stores"])
        ws.append(["Daily sales register - 1 Apr 2026 to 28 Sep 2026"])
        ws.append(["Figures in INR. Prepared by the shop accountant. The last row is the total, not a bill."])
        ws.append([])
        ws.append([None, "Bill details", None, None, "Amounts", None, None])        # a merged group header above the real header
        ws.append(head)
        ws.merge_cells("A1:G1")
        ws.merge_cells("B5:D5")
        ws.merge_cells("E5:G5")
        for row in data:
            ws.append(row)
        ws.append([])
        ws.append(["TOTAL", None, None, None, totals["qty"], None, round(totals["amount"], 2)])
        wb.properties.created = wb.properties.modified = dt.datetime(2026, 10, 1)
        wb.save(path)
        freeze_xlsx(path)

    f = "kirana_register.xlsx"

    def clean(c: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        df = c[f]
        df = df[df["Bill No"].notna() & df["Bill No"].astype(str).str.upper().ne("TOTAL")]
        return {f: df.reset_index(drop=True)}

    return spec(id="R08", dir="R08_kirana_register_totals_row", business="kirana store", kind="sales",
                tests="three notes above the table, a merged group header row above the real header, a blank row, and a TOTAL row at the bottom",
                app_should="find the real header row, drop the TOTAL row from the data, and never count the total as one more bill",
                files=[File(f, writer=writer, reader="excel", options={"skiprows": 5},
                            note="rows 1-3 are notes, row 5 is a merged group header, row 6 is the real header, the last row is TOTAL")],
                clean=clean,
                cleaning=["read with skiprows=5 so row 6 is the header", "dropped the blank row and the TOTAL row at the bottom"],
                amount=(f, "Amount", "revenue, with the TOTAL row excluded"), category=(f, "Item", "Amount"),
                keys={f: ["Bill No", "Item"]},
                also={"bills": lambda c: int(len(c[f])),
                      "total_row_amount_stated_in_the_file": num(totals["amount"]),
                      "total_row_quantity_stated_in_the_file": int(totals["qty"]),
                      "amount_total_if_the_TOTAL_row_is_wrongly_counted": num(totals["amount"] * 2),
                      "note": "the computed Amount total must equal the TOTAL row's figure, and the bill count must be 520, not 521"})


def r09() -> dict[str, Any]:
    r, _ = rng_for(19)
    cat = CATALOGUE["electronics seller"]
    reasons = {"Returned": ["Damaged in transit", "Wrong item sent", "Not as described", "Size/fit issue"],
               "Refunded": ["Battery fault", "Stopped working", "Duplicate order"],
               "Cancelled": ["Customer changed mind", "Out of stock", "Address not serviceable", "Payment failed"],
               "Exchanged": ["Colour change", "Model upgrade"]}
    rows = []
    for n in range(1, 841):
        item, price, cost, group, code = r.choice(cat)
        q = r.choice([1, 1, 2])
        amt = round(price * q, 2)
        d = dt.date(2026, 1, 15) + dt.timedelta(days=r.randint(0, 220))
        status = r.choices(["Delivered", "Returned", "Cancelled", "Refunded", "Exchanged"], [70, 12, 9, 6, 3])[0]
        refund = amt if status in ("Returned", "Refunded") else 0.0
        rows.append({"order_id": f"EL{70000 + n}", "order_date": d.isoformat(), "item": item, "sku": code, "category": group,
                     "quantity": q, "amount": amt, "status": status,
                     "return_reason": r.choice(reasons[status]) if status in reasons else "",
                     "return_date": (d + dt.timedelta(days=r.randint(3, 20))).isoformat() if status in reasons else "",
                     "refund_amount": refund, "courier": r.choice(["Delhivery", "BlueDart", "India Post", "Ecom"])})
    df = pd.DataFrame(rows)
    f = "electronics_returns.csv"
    return spec(id="R09", dir="R09_electronics_returns", business="electronics seller", kind="returns",
                tests="a status column with five states and a reason column that is deliberately blank for delivered orders",
                app_should="treat the blank reasons as 'not applicable' rather than missing data, and report net revenue after refunds alongside gross",
                files=[File(f, df=df, note="return_reason and return_date are blank exactly on the Delivered rows, where they do not apply")],
                amount=(f, "amount", "gross order value, every status included"), category=(f, "category", "amount"),
                keys={f: ["order_id", "sku", "status", "return_reason"]},
                extra_sums=[(f, "refund_amount", "total_refunded", "amount refunded on returns and refunds")],
                extra_tops=[(f, "return_reason", "amount", "top_3_return_reasons_by_amount")],
                also={"status_counts": lambda c: {k: int(v) for k, v in c[f]["status"].value_counts().items()},
                      "net_revenue_after_refunds": lambda c: num(c[f]["amount"].sum() - c[f]["refund_amount"].sum()),
                      "revenue_of_delivered_only": lambda c: num(c[f].loc[c[f]["status"] == "Delivered", "amount"].sum()),
                      "return_rate_pct_of_orders": lambda c: num(100 * c[f]["status"].isin(["Returned", "Refunded"]).sum() / len(c[f])),
                      "rows_where_reason_is_not_applicable": lambda c: int((c[f]["status"] == "Delivered").sum())})


# ================================================================== the other things a small vendor keeps, with no sales column (R10-R17)
BRANDS = ["Aashirvaad", "Tata", "Fortune", "Everest", "Nestle", "Parle", "Amul", "Saffola", "Dabur", "Patanjali", "Local", "Surf"]
SIZES = ["100g", "250g", "500g", "1kg", "1L", "2kg", "5kg", "pack of 6", "pack of 12"]


def r10() -> dict[str, Any]:
    r, _ = rng_for(20)
    base = [c[0].split()[0] for c in CATALOGUE["kirana store"]]
    rows = []
    for n in range(1, 421):
        item = f"{r.choice(BRANDS)} {r.choice(base)} {r.choice(SIZES)}"
        onhand = r.choice([0, 0, 1, 2, 4, 6, 8, 12, 18, 25, 40, 60, 90])
        reorder = r.choice([3, 5, 10, 12, 20])
        rows.append({"item_code": f"SKU-{n:04d}", "item": item, "category": r.choice(["Staples", "Snacks", "Household", "Beverages", "Dairy", "Personal Care"]),
                     "unit": r.choice(["pcs", "kg", "ltr", "pack"]), "qty_on_hand": onhand, "reorder_level": reorder,
                     "last_restocked": (dt.date(2026, 9, 1) + dt.timedelta(days=r.randint(0, 55))).isoformat(),
                     "damaged_units": r.choice([0, 0, 0, 0, 1, 2, 3]), "shelf": f"{r.choice('ABCDE')}{r.randint(1, 9)}",
                     "supplier": r.choice(["Shree Distributors", "Pune Wholesale", "Metro Cash", "Direct"])})
    df = pd.DataFrame(rows)
    f = "kirana_inventory.csv"
    return spec(id="R10", dir="R10_kirana_inventory", business="kirana store", kind="inventory",
                tests="stock levels with no money column at all: quantities, reorder levels, restock dates and damage",
                app_should="recognise this is stock and not sales, report what is below its reorder level and what is damaged, and never report revenue",
                files=[File(f, df=df, note="no price, cost or amount column exists; qty_on_hand is the main measure")],
                amount=(f, "qty_on_hand", "units in stock (a quantity, not money)"), category=(f, "category", "qty_on_hand"),
                keys={f: ["item_code", "category", "supplier"]},
                extra_sums=[(f, "damaged_units", "total_damaged_units", "units written off as damaged")],
                also={"money_columns_present": [], "items_at_or_below_reorder_level": lambda c: int((c[f]["qty_on_hand"] <= c[f]["reorder_level"]).sum()),
                      "items_out_of_stock": lambda c: int((c[f]["qty_on_hand"] == 0).sum()),
                      "items_with_any_damage": lambda c: int((c[f]["damaged_units"] > 0).sum())})


def r11() -> dict[str, Any]:
    r, _ = rng_for(21)
    cat = CATALOGUE["bakery"]
    mats = ["Maida 50kg", "Butter 5kg", "Sugar 25kg", "Cocoa 2kg", "Yeast 500g", "Cream 1L", "Eggs tray", "Packaging box", "Almonds 1kg", "Milk 10L"]
    rows = []
    for n in range(1, 701):
        item = r.choice(mats)
        qty = r.randint(1, 30)
        unit_cost = round(r.choice([180, 420, 1250, 890, 310, 240, 160, 95, 1100, 520]) * r.uniform(0.95, 1.08), 2)
        d = dt.date(2026, 1, 8) + dt.timedelta(days=r.randint(0, 260))
        status = r.choices(["Paid", "Pending", "Partially paid"], [68, 22, 10])[0]
        rows.append({"purchase_id": f"PO-{3000 + n}", "purchase_date": d.isoformat(), "supplier": r.choice(["Shree Flour Mills", "Dairy Best", "Sweet Supplies Co", "PackRight", "Nuts & More"]),
                     "item": item, "quantity": qty, "unit": r.choice(["bag", "box", "can", "tray"]), "unit_cost": unit_cost,
                     "total_cost": round(unit_cost * qty, 2), "payment_status": status,
                     "invoice_no": f"INV/{d.year}/{r.randint(1000, 9999)}", "due_date": (d + dt.timedelta(days=30)).isoformat()})
    df = pd.DataFrame(rows)
    f = "bakery_purchases.csv"
    return spec(id="R11", dir="R11_bakery_purchases", business="bakery", kind="purchases",
                tests="money flowing out, not in: supplier purchases with unit cost and payment status",
                app_should="call this spending, not revenue, and report what is still unpaid by supplier",
                files=[File(f, df=df, note="total_cost = quantity x unit_cost; no selling price anywhere")],
                amount=(f, "total_cost", "money spent on purchases"), category=(f, "supplier", "total_cost"),
                keys={f: ["purchase_id", "supplier", "item", "invoice_no"]},
                extra_tops=[(f, "item", "total_cost", "top_3_items_by_spend"), (f, "payment_status", "total_cost", "spend_by_payment_status")],
                also={"this_is_spending_not_revenue": True,
                      "unpaid_amount": lambda c: num(c[f].loc[c[f]["payment_status"] != "Paid", "total_cost"].sum()),
                      "unpaid_purchase_count": lambda c: int((c[f]["payment_status"] != "Paid").sum())})


SPELLINGS = {"Electricity": ["Electricity", "electricity", "Elec.", "Electricty", "ELECTRICITY", "Electricity Bill"],
             "Rent": ["Rent", "rent", "Shop Rent", "RENT", "Rent "],
             "Staff salary": ["Salaries", "Salary", "salaries", "Staff Salary", "staff salaries"],
             "Products and stock": ["Products", "Product Purchase", "products", "Product purchase", "Stock"],
             "Internet": ["Internet", "Internet Bill", "wifi", "WiFi", "internet"],
             "Maintenance": ["Maintenance", "maintainance", "Maintenence", "maintenance"],
             "Marketing": ["Marketing", "Ads", "marketing", "Advertising"]}
CANON = {v: k for k, vs in SPELLINGS.items() for v in vs}


def r12() -> dict[str, Any]:
    r, _ = rng_for(22)
    typical = {"Electricity": 4500, "Rent": 35000, "Staff salary": 48000, "Products and stock": 12000, "Internet": 1200, "Maintenance": 3000, "Marketing": 6000}
    rows = []
    for n in range(1, 901):
        canon = r.choice(list(SPELLINGS))
        rows.append({"expense_date": (dt.date(2026, 1, 1) + dt.timedelta(days=r.randint(0, 280))).isoformat(),
                     "category": r.choice(SPELLINGS[canon]), "amount": round(typical[canon] * r.uniform(0.6, 1.4), 2),
                     "payment_method": r.choice(["UPI", "Cash", "Bank transfer", "Card"]),
                     "paid_to": r.choice(["MSEDCL", "Landlord", "Staff", "Beauty Supplies Co", "Airtel", "Handyman", "Ad agency"]),
                     "notes": r.choice(["", "", "monthly", "urgent", "part payment"])})
    df = pd.DataFrame(rows)
    f = "salon_expenses.csv"

    def clean(c: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        df = c[f].copy()
        df["category_clean"] = df["category"].map(lambda v: CANON.get(str(v).strip(), CANON.get(str(v), str(v))))
        return {f: df}

    return spec(id="R12", dir="R12_salon_expenses", business="salon", kind="expenses",
                tests="the same expense category spelled several ways (case, abbreviations, a typo, a trailing space), so the raw grouping is wrong",
                app_should="group the spellings together before ranking, and say it did; the raw top 3 and the tidied top 3 are different",
                files=[File(f, df=df, note=f"{len(CANON)} spellings for {len(SPELLINGS)} real categories, including 'Electricty' and 'maintainance'")],
                clean=clean, cleaning=["mapped the category spellings onto seven canonical names (category_clean)"],
                amount=(f, "amount", "money spent"), category=(f, "category_clean", "amount"),
                keys={f: ["category", "category_clean", "payment_method"]},
                extra_tops=[(f, "category", "amount", "top_3_by_category_as_written_WRONG")],
                also={"distinct_spellings": lambda c: int(c[f]["category"].nunique()), "real_categories": len(SPELLINGS),
                      "spelling_groups": SPELLINGS,
                      "total_by_tidied_category": lambda c: {k: num(v) for k, v in c[f].groupby("category_clean")["amount"].sum().sort_values(ascending=False).items()}})


def r13() -> dict[str, Any]:
    r, _ = rng_for(23)
    rows = []
    for n in range(1, 61):
        name, email, phone = person(r)
        role = r.choice(["Sales associate", "Tailor", "Store manager", "Cashier", "Helper", "Visual merchandiser"])
        pay = {"Sales associate": 18000, "Tailor": 22000, "Store manager": 38000, "Cashier": 16000, "Helper": 12000, "Visual merchandiser": 26000}[role]
        rows.append({"employee_id": f"EMP{n:03d}", "employee_name": name, "role": role, "branch": r.choice(["FC Road", "Koregaon Park", "Baner"]),
                     "phone": phone, "email": email, "date_of_birth": (dt.date(1985, 1, 1) + dt.timedelta(days=r.randint(0, 5000))).isoformat(),
                     "joining_date": (dt.date(2021, 1, 1) + dt.timedelta(days=r.randint(0, 1700))).isoformat(),
                     "monthly_pay": round(pay * r.uniform(0.92, 1.15), 2), "days_present": r.randint(17, 26), "hours_worked": r.randint(120, 210),
                     "emergency_contact": f"{r.choice('6789')}{r.randint(10 ** 8, 10 ** 9 - 1)}", "city": r.choice(CITIES)})
    df = pd.DataFrame(rows)
    f = "boutique_staff.csv"
    return spec(id="R13", dir="R13_boutique_staff", business="clothing boutique", kind="staff",
                tests="personal-looking columns (name, phone, e-mail, date of birth, emergency contact) next to real measures (pay, attendance, hours)",
                app_should="treat phone, e-mail, date of birth and the emergency number as identifiers, never as numbers to average or chart, and keep names out of the AI's view",
                files=[File(f, df=df, note="60 made-up staff records; phone and emergency_contact are ten-digit numbers that must not be treated as measures")],
                amount=(f, "monthly_pay", "monthly payroll"), category=(f, "role", "monthly_pay"),
                keys={f: ["employee_id", "role", "branch"]},
                extra_sums=[(f, "hours_worked", "total_hours_worked", "hours worked in the month")],
                also={"columns_that_must_not_be_measured": ["phone", "emergency_contact", "date_of_birth", "employee_id"],
                      "personal_name_columns": ["employee_name"],
                      "mean_days_present": lambda c: num(c[f]["days_present"].mean()),
                      "headcount": lambda c: int(c[f]["employee_id"].nunique())})


def r14() -> dict[str, Any]:
    r, _ = rng_for(24)
    recipes = {"Mango Pickle 500g": ["Raw mango", "Mustard oil", "Spice mix", "Salt"], "Besan Laddu 500g": ["Besan", "Ghee", "Sugar", "Cardamom"],
               "Pure Ghee 500ml": ["Butter", "Fuel"], "Chakli 250g": ["Rice flour", "Besan", "Oil", "Spice mix"],
               "Idli Batter 1kg": ["Rice", "Urad dal", "Salt"], "Dry Fruit Chikki": ["Jaggery", "Cashew", "Almond", "Ghee"]}
    rows, bid = [], 0
    for day in range(0, 195, 2):
        for product in r.sample(list(recipes), r.choice([1, 2, 2, 3])):
            bid += 1
            output = r.choice([40, 50, 60, 80, 100])
            waste = r.choice([0, 0, 1, 2, 3, 5])
            d = (dt.date(2026, 3, 2) + dt.timedelta(days=day)).isoformat()
            for mat in recipes[product]:
                rows.append({"batch_id": f"BT-{bid:04d}", "production_date": d, "product": product, "material": mat,
                             "quantity_used": round(r.uniform(0.5, 18.0), 2), "unit": r.choice(["kg", "ltr", "g"]),
                             "output_units": output, "wastage_units": waste, "operator": r.choice(["Lakshmi", "Asha", "Ritu"]),
                             "batch_status": r.choices(["Completed", "Completed", "Completed", "Rework"], [1, 1, 1, 1])[0]})
    df = pd.DataFrame(rows)
    f = "food_production_batches.csv"
    return spec(id="R14", dir="R14_home_food_production", business="home-based food seller", kind="production",
                tests="production batches: several material rows per batch, with the batch-level output and wastage repeated on each of those rows",
                app_should="count batches by distinct batch_id and not add output_units up once per material row",
                files=[File(f, df=df, note="output_units, wastage_units and batch_status repeat on every material row of the same batch")],
                amount=(f, "quantity_used", "material consumed (a quantity, not money)"), category=(f, "product", "quantity_used"),
                keys={f: ["batch_id", "product", "material", "operator"]},
                extra_tops=[(f, "material", "quantity_used", "top_3_materials_by_quantity_used")],
                also={"material_rows": lambda c: int(len(c[f])), "distinct_batches": lambda c: int(c[f]["batch_id"].nunique()),
                      "output_units_per_distinct_batch": lambda c: int(c[f].drop_duplicates("batch_id")["output_units"].sum()),
                      "output_units_summed_naively_over_all_rows": lambda c: int(c[f]["output_units"].sum()),
                      "wastage_units_per_distinct_batch": lambda c: int(c[f].drop_duplicates("batch_id")["wastage_units"].sum()),
                      "money_columns_present": []})


def r15() -> dict[str, Any]:
    r, _ = rng_for(25)
    cat = CATALOGUE["handicrafts seller"]
    rows = []
    for n in range(1, 501):
        name, _, phone = person(r)
        buys = r.choice([1, 1, 1, 2, 2, 3, 4, 6, 9])
        rows.append({"Grahak ID": f"GRA-{n:04d}", "Grahak ka naam": name, "Mobile no.": phone, "Shehar": r.choice(CITIES),
                     "Pehli kharid ki tarikh": (dt.date(2024, 1, 1) + dt.timedelta(days=r.randint(0, 900))).strftime("%d/%m/%Y"),
                     "Kul kharid (sankhya)": buys, "Kul rashi": round(sum(r.choice(cat)[1] for _ in range(buys)) * r.uniform(0.95, 1.1), 2),
                     "Pasandida saman": r.choice(cat)[0], "Sthiti": r.choice(["Sakriya", "Sakriya", "Nishkriya", "Naya"]),
                     "Bhugtan tarika": r.choice(["UPI", "Cash", "Card", "COD"])})
    df = pd.DataFrame(rows)
    f = "handicraft_grahak.csv"
    return spec(id="R15", dir="R15_handicrafts_customers", business="handicrafts seller", kind="customers",
                tests="column names in transliterated Hindi, phone numbers that must stay text, and a lifetime-spend column that is not per-order revenue",
                app_should="work out what each Hindi-named column means from its values, not its name, and not treat 'Kul rashi' (lifetime spend) as this period's sales",
                files=[File(f, fmt="csv-bom", df=df, note="UTF-8 with BOM, as a Windows spreadsheet saves it; headers are transliterated Hindi")],
                amount=(f, "Kul rashi", "lifetime spend per customer, not sales in a period"),
                category=(f, "Shehar", "Kul rashi"), keys={f: ["Grahak ID", "Mobile no.", "Shehar", "Sthiti"]},
                extra_sums=[(f, "Kul kharid (sankhya)", "total_purchase_count", "purchases made, counted across all customers")],
                extra_tops=[(f, "Pasandida saman", "Kul rashi", "top_3_favourite_items_by_lifetime_spend")],
                also={"column_meanings": {"Grahak ID": "customer id", "Grahak ka naam": "customer name", "Mobile no.": "phone", "Shehar": "city",
                                          "Pehli kharid ki tarikh": "date of first purchase", "Kul kharid (sankhya)": "number of purchases",
                                          "Kul rashi": "total amount spent (lifetime)", "Pasandida saman": "favourite item", "Sthiti": "status", "Bhugtan tarika": "payment method"},
                      "repeat_customers": lambda c: int((c[f]["Kul kharid (sankhya)"] > 1).sum()),
                      "mean_lifetime_spend": lambda c: num(c[f]["Kul rashi"].mean())})


def r16() -> dict[str, Any]:
    r, _ = rng_for(26)
    cat = CATALOGUE["electronics seller"]
    rows = []
    for n in range(1, 901):
        item, price, _, _, code = r.choice(cat)
        q = r.choice([1, 1, 2])
        sale = round(price * q, 2)
        commission = round(sale * 0.11, 2)
        shipping = round(38 + 12 * q, 2)
        fixed = 22.0
        gst = round((commission + shipping + fixed) * 0.18, 2)
        adj = 0.0
        if n % 10 == 0:
            adj = -round(sale * r.choice([0.05, 0.1, 1.0]), 2)                 # a clawback, a penalty, or a full reversal
        elif n % 31 == 0:
            adj = round(r.choice([25, 50, 120]), 2)                             # a credit back from the marketplace
        d = dt.date(2026, 5, 1) + dt.timedelta(days=r.randint(0, 150))
        rows.append({"settlement_id": f"STL-{d.strftime('%Y%m')}-{r.randint(1, 4)}", "order_id": f"MP{900000 + n}", "order_date": d.isoformat(),
                     "settlement_date": (d + dt.timedelta(days=r.randint(7, 15))).isoformat(), "sku": code, "item": item, "quantity": q,
                     "sale_amount": sale, "commission": commission, "shipping_fee": shipping, "fixed_fee": fixed, "gst_on_fees": gst,
                     "adjustment": adj, "net_payout": round(sale - commission - shipping - fixed - gst + adj, 2)})
    df = pd.DataFrame(rows)
    f = "marketplace_settlement.csv"
    return spec(id="R16", dir="R16_marketplace_payout", business="electronics seller", kind="payout",
                tests="a settlement statement where the money received is not the sale amount: four fee columns, GST on the fees, and negative adjustments",
                app_should="report net payout and sale amount as different things, and not quietly treat sale_amount as what the seller received",
                files=[File(f, df=df, note="net_payout = sale_amount - commission - shipping_fee - fixed_fee - gst_on_fees + adjustment; adjustment is negative on 90 rows")],
                amount=(f, "net_payout", "money actually received after fees and adjustments"),
                category=(f, "item", "net_payout"), keys={f: ["order_id", "sku", "settlement_id"]},
                extra_sums=[(f, "sale_amount", "total_sale_amount", "what the customer paid, before fees"),
                            (f, "commission", "total_commission", "marketplace commission"),
                            (f, "shipping_fee", "total_shipping_fee", "shipping charged by the marketplace"),
                            (f, "fixed_fee", "total_fixed_fee", "fixed fee per order"),
                            (f, "gst_on_fees", "total_gst_on_fees", "GST charged on the fees"),
                            (f, "adjustment", "total_adjustment", "adjustments, mostly negative")],
                also={"negative_adjustment_rows": lambda c: int((c[f]["adjustment"] < 0).sum()),
                      "positive_adjustment_rows": lambda c: int((c[f]["adjustment"] > 0).sum()),
                      "total_fees": lambda c: num(c[f][["commission", "shipping_fee", "fixed_fee", "gst_on_fees"]].sum().sum()),
                      "fees_as_pct_of_sale": lambda c: num(100 * c[f][["commission", "shipping_fee", "fixed_fee", "gst_on_fees"]].sum().sum() / c[f]["sale_amount"].sum()),
                      "net_payout_check": lambda c: num((c[f]["sale_amount"] - c[f]["commission"] - c[f]["shipping_fee"] - c[f]["fixed_fee"] - c[f]["gst_on_fees"] + c[f]["adjustment"]).sum())})


def r17() -> dict[str, Any]:
    r, g = rng_for(27)
    rows = []
    for day in range(180):
        d = (dt.date(2026, 3, 1) + dt.timedelta(days=day))
        for channel, base_spend, ctr in (("Instagram", 900, 0.018), ("Google", 1400, 0.032), ("WhatsApp broadcast", 150, 0.055), ("Local flyers", 400, 0.004)):
            spend = round(base_spend * r.uniform(0.5, 1.6), 2)
            impressions = int(spend * r.uniform(18, 42))
            clicks = int(impressions * ctr * r.uniform(0.7, 1.3))
            attempted = r.randint(4, 40)
            rows.append({"date": d.isoformat(), "channel": channel, "ad_spend": spend, "impressions": impressions, "clicks": clicks,
                         "orders_attributed": max(0, int(clicks * r.uniform(0.02, 0.09))), "deliveries_attempted": attempted,
                         "deliveries_failed": min(attempted, max(0, int(attempted * r.uniform(0, 0.18)))),
                         "avg_delivery_days": round(r.uniform(1.2, 6.5), 1), "weekday": d.strftime("%A")})
    df = pd.DataFrame(rows)
    f = "marketing_delivery.csv"
    return spec(id="R17", dir="R17_marketing_delivery", business="clothing boutique", kind="marketing",
                tests="two different subjects in one file: daily ad spend by channel, and delivery performance",
                app_should="keep spend, clicks, orders and delivery failures apart as separate measures, and not call ad spend revenue",
                files=[File(f, df=df, note="one row per day per channel, 180 days x 4 channels; no sales amount anywhere")],
                amount=(f, "ad_spend", "money spent on advertising"), category=(f, "channel", "ad_spend"),
                keys={f: ["channel", "date", "weekday"]},
                extra_sums=[(f, "clicks", "total_clicks", "clicks"), (f, "orders_attributed", "total_orders_attributed", "orders credited to a channel"),
                            (f, "deliveries_failed", "total_failed_deliveries", "deliveries that failed")],
                also={"this_is_spending_not_revenue": True, "days": 180, "channels": 4,
                      "failed_delivery_rate_pct": lambda c: num(100 * c[f]["deliveries_failed"].sum() / c[f]["deliveries_attempted"].sum()),
                      "mean_avg_delivery_days": lambda c: num(c[f]["avg_delivery_days"].mean()),
                      "cost_per_attributed_order": lambda c: num(c[f]["ad_spend"].sum() / c[f]["orders_attributed"].sum())})


# ================================================================== connected sets (M01-M09)
def m01() -> dict[str, Any]:
    r, _ = rng_for(31)
    cat = CATALOGUE["bakery"]
    customers = []
    for n in range(1, 221):
        name, email, phone = person(r)
        customers.append({"customer_id": f"C{n:04d}", "customer_name": name, "email": email, "phone": phone, "city": r.choice(CITIES),
                          "joined_on": (dt.date(2024, 6, 1) + dt.timedelta(days=r.randint(0, 700))).isoformat(),
                          "loyalty_tier": r.choice(["Bronze", "Bronze", "Silver", "Gold"])})
    known = [c["customer_id"] for c in customers]
    orphans = [f"C9{i:03d}" for i in range(1, 7)]                              # six ids that are in no customer record
    orders = []
    for n in range(1, 901):
        item, price, cost, group, code = r.choice(cat)
        q = r.choice([1, 1, 2, 3])
        cid = r.choice(orphans) if n % 50 == 0 else r.choice(known)            # 18 orders point at an unknown customer
        orders.append({"order_id": f"BO{n:05d}", "order_date": (dt.date(2026, 2, 1) + dt.timedelta(days=r.randint(0, 230))).isoformat(),
                       "customer_id": cid, "item": item, "item_code": code, "category": group, "quantity": q,
                       "amount": round(price * q, 2), "payment_mode": r.choice(["UPI", "Cash", "Card"])})
    o, c = "orders.csv", "customers.csv"
    return spec(id="M01", dir="M01_bakery_orders_customers", business="bakery", kind="connected (2 files)",
                tests="two files that join on customer_id, with a handful of orders pointing at customers who are not in the customer file",
                app_should="find the customer_id link by itself, say how many orders have no matching customer, and not silently drop them from the revenue total",
                files=[File(o, df=pd.DataFrame(orders), note="18 orders use one of six customer ids that do not exist in customers.csv"),
                       File(c, df=pd.DataFrame(customers), note="220 customers; some have never ordered")],
                amount=(o, "amount", "revenue"), category=(o, "category", "amount"),
                keys={o: ["order_id", "customer_id"], c: ["customer_id", "city"]},
                joins=[{"left": o, "right": c, "on": "customer_id", "note": "orders -> customers; the orphan ids are C9001-C9006"}],
                also={"orders_with_no_matching_customer": lambda cl: int(cl[o]["customer_id"].isin(orphans).sum()),
                      "orphan_customer_ids_used_by_an_order": lambda cl: sorted(set(cl[o]["customer_id"]) - set(cl[c]["customer_id"])),
                      "revenue_of_orphan_orders": lambda cl: num(cl[o].loc[cl[o]["customer_id"].isin(orphans), "amount"].sum()),
                      "customers_who_never_ordered": lambda cl: int((~cl[c]["customer_id"].isin(cl[o]["customer_id"])).sum())})


def m02() -> dict[str, Any]:
    r, _ = rng_for(32)
    cat = CATALOGUE["clothing boutique"]
    products = [{"product_id": f"P{i:03d}", "product_name": p[0], "category": p[3], "list_price": p[1], "cost_price": p[2],
                 "fabric": r.choice(["Cotton", "Silk", "Rayon", "Denim", "Georgette"])} for i, p in enumerate(cat, start=101)]
    products.append({"product_id": "P999", "product_name": "Discontinued Scarf", "category": "Accessories", "list_price": 300, "cost_price": 160, "fabric": "Cotton"})
    customers = []
    for n in range(1, 181):
        name, email, phone = person(r)
        customers.append({"customer_id": f"B{n:04d}", "customer_name": name, "email": email, "phone": phone,
                          "city": r.choice(CITIES), "tier": r.choice(["Regular", "Regular", "Premium"])})
    customers.append(dict(customers[40], customer_name=customers[40]["customer_name"] + " (duplicate record)"))   # B0041 appears twice
    dup_key = customers[40]["customer_id"]
    known_c = [c["customer_id"] for c in customers[:-1]]
    known_p = [p["product_id"] for p in products[:-1]]
    orphan_c, orphan_p = ["B9001", "B9002", "B9003", "B9004", "B9005"], ["P777", "P778", "P779", "P780"]
    orders = []
    for n in range(1, 961):
        pid = r.choice(orphan_p) if n % 80 == 0 else r.choice(known_p)
        cid = r.choice(orphan_c) if n % 64 == 0 else r.choice(known_c)
        price = next((p["list_price"] for p in products if p["product_id"] == pid), 500)
        q = r.choice([1, 1, 1, 2])
        orders.append({"order_id": f"BQO{n:05d}", "order_date": (dt.date(2026, 1, 10) + dt.timedelta(days=r.randint(0, 250))).isoformat(),
                       "customer_id": cid, "product_id": pid, "quantity": q, "amount": round(price * q, 2),
                       "channel": r.choice(["Store", "Instagram", "Website"])})
    o, c, p = "orders.csv", "customers.csv", "products.csv"
    return spec(id="M02", dir="M02_boutique_three_files", business="clothing boutique", kind="connected (3 files)",
                tests="three files, orphan ids on both links, and one customer_id that appears twice in the customer file so a careless join inflates the row count",
                app_should="join on the right keys, report the orphans, and notice that the repeated customer_id makes the joined table longer than the order table",
                files=[File(o, df=pd.DataFrame(orders), note="12 orders use unknown product ids, 15 use unknown customer ids"),
                       File(c, df=pd.DataFrame(customers), note=f"181 rows for 180 customers: {dup_key} appears twice"),
                       File(p, df=pd.DataFrame(products), note="13 products; P999 was never ordered")],
                amount=(o, "amount", "revenue"), keys={o: ["order_id", "customer_id", "product_id"], c: ["customer_id"], p: ["product_id", "category"]},
                key_duplicates={c: ["customer_id"]},
                joins=[{"left": o, "right": c, "on": "customer_id", "note": "the repeated customer_id is why the inner join has more rows than the matching orders"},
                       {"left": o, "right": p, "on": "product_id", "note": "orders -> products"}],
                also={"duplicated_customer_id": dup_key,
                      "orphan_customer_ids_in_orders": lambda cl: sorted(set(cl[o]["customer_id"]) - set(cl[c]["customer_id"])),
                      "orphan_product_ids_in_orders": lambda cl: sorted(set(cl[o]["product_id"]) - set(cl[p]["product_id"])),
                      "orders_with_unknown_customer": lambda cl: int(cl[o]["customer_id"].isin(orphan_c).sum()),
                      "orders_with_unknown_product": lambda cl: int(cl[o]["product_id"].isin(orphan_p).sum()),
                      "products_never_ordered": lambda cl: sorted(set(cl[p]["product_id"]) - set(cl[o]["product_id"])),
                      "revenue_joined_to_a_known_product": lambda cl: num(cl[o].loc[cl[o]["product_id"].isin(cl[p]["product_id"]), "amount"].sum())})


def m03() -> dict[str, Any]:
    r, _ = rng_for(33)
    cat = CATALOGUE["kirana store"]
    items = [{"item_code": p[4], "item": p[0], "category": p[3], "unit": r.choice(["pcs", "kg", "pack"])} for p in cat]
    customers = [{"customer_id": f"K{n:03d}", "customer_name": person(r)[0], "area": r.choice(AREAS), "khata": r.choice(["Yes", "No", "No"])} for n in range(1, 131)]
    known_c = [c["customer_id"] for c in customers]
    orders, lines = [], []
    for n in range(1, 641):
        oid = f"KO{n:05d}"
        cid = "K999" if n % 80 == 0 else r.choice(known_c)                     # eight orders point at a customer who is not listed
        picks = [(r.choice(cat), r.randint(1, 6)) for _ in range(r.choice([1, 2, 2, 3]))]
        total = round(sum(p[1] * q for p, q in picks), 2)
        orders.append({"order_id": oid, "order_date": (dt.date(2026, 4, 1) + dt.timedelta(days=r.randint(0, 170))).isoformat(),
                       "customer_id": cid, "amount": total, "payment_mode": r.choice(["Cash", "UPI", "Khata"])})
        for k, (p, q) in enumerate(picks, start=1):
            lines.append({"order_id": oid, "line_no": k, "item_code": p[4], "qty": q, "rate": p[1], "line_amount": round(p[1] * q, 2)})
    bills = [{"bill_month": (dt.date(2025, 10, 1) + dt.timedelta(days=31 * m)).strftime("%b-%Y"), "meter_no": "MH-4471902",
              "units_consumed": r.randint(180, 520), "amount_paid": round(r.uniform(1400, 4800), 2),
              "paid_on": (dt.date(2025, 10, 18) + dt.timedelta(days=31 * m)).isoformat()} for m in range(14)]
    o, l, i, c, e = "orders.csv", "order_lines.csv", "items.csv", "customers.csv", "electricity_bills.csv"
    return spec(id="M03", dir="M03_kirana_five_files", business="kirana store", kind="connected (5 files, one unrelated)",
                tests="five files where four form a chain (orders -> order_lines -> items, orders -> customers) and the fifth, the electricity bill, joins to nothing",
                app_should="link the four that connect, say plainly that the electricity bill shares no key with the rest, and not force a join",
                files=[File(o, df=pd.DataFrame(orders), note="8 orders use customer_id K999, which is not in customers.csv"),
                       File(l, df=pd.DataFrame(lines), note="one row per item on an order"),
                       File(i, df=pd.DataFrame(items), note="the item master, 12 items"),
                       File(c, df=pd.DataFrame(customers), note="130 customers"),
                       File(e, df=pd.DataFrame(bills), note="joins to nothing: no order id, item code or customer id")],
                amount=(o, "amount", "revenue from the order table"), keys={o: ["order_id", "customer_id"], l: ["order_id", "item_code"], i: ["item_code"], c: ["customer_id"]},
                extra_sums=[(l, "line_amount", "order_lines_total", "revenue added up from the line table instead")],
                joins=[{"left": l, "right": o, "on": "order_id", "note": "lines -> orders"},
                       {"left": l, "right": i, "on": "item_code", "note": "lines -> item master"},
                       {"left": o, "right": c, "on": "customer_id", "note": "orders -> customers; K999 is the orphan"}],
                also={"file_that_connects_to_nothing": e, "shared_columns_of_that_file": [],
                      "orders_with_unknown_customer": lambda cl: int((cl[o]["customer_id"] == "K999").sum()),
                      "order_total_matches_line_total": lambda cl: bool(round(cl[o]["amount"].sum(), 2) == round(cl[l]["line_amount"].sum(), 2)),
                      "electricity_total_paid": lambda cl: num(cl[e]["amount_paid"].sum())})


def m04() -> dict[str, Any]:
    r, _ = rng_for(34)
    cat = CATALOGUE["handicrafts seller"]
    a_rows, b_rows = [], []
    a_total = b_total = 0.0
    for n in range(1, 521):
        item, price, _, group, code = r.choice(cat)
        q = r.choice([1, 1, 2])
        amt = round(price * q, 2)
        a_total += amt
        d = dt.date(2026, 2, 1) + dt.timedelta(days=r.randint(0, 200))
        a_rows.append({"Order No": f"CB-{20000 + n}", "Order Date": d.strftime("%d-%m-%Y"), "Item": item, "Qty": q,
                       "Amount": f"Rs. {indian(amt)}", "Buyer City": r.choice(CITIES), "Status": r.choice(["Delivered", "Delivered", "In transit", "Cancelled"])})
    for n in range(1, 441):
        item, price, _, group, code = r.choice(cat)
        q = r.choice([1, 1, 2, 3])
        amt = round(price * q, 2)
        b_total += amt
        d = dt.datetime(2026, 2, 1, 10, 0) + dt.timedelta(days=r.randint(0, 200), minutes=r.randint(0, 600))
        b_rows.append({"order_id": f"SL{700000 + n}", "created_at": d.strftime("%Y-%m-%dT%H:%M:%S"), "sku": code, "units": q,
                       "total_inr": amt, "city": r.choice(CITIES), "fulfilment_status": r.choice(["fulfilled", "fulfilled", "pending", "cancelled"])})
    a, b = "platform_craftbazaar.csv", "platform_shopline.csv"

    def clean(c: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        da = c[a].copy()
        da["amount_parsed"] = da["Amount"].map(money)
        da["date_parsed"] = da["Order Date"].map(parse_date_any).astype(str)
        return {a: da, b: c[b]}

    return spec(id="M04", dir="M04_handicrafts_two_platforms", business="handicrafts seller", kind="connected (2 files, same business)",
                tests="the same shop's sales from two platforms: different column names, different date formats, amounts as text on one side and numbers on the other, and no shared id",
                app_should="see that the two files are the same kind of thing and can be stacked, match the columns across them, and give one combined total without inventing a join key",
                files=[File(a, df=pd.DataFrame(a_rows), note="'Order No', 'Order Date' as dd-mm-yyyy, 'Amount' as text like 'Rs. 1,250'"),
                       File(b, df=pd.DataFrame(b_rows), note="order_id, created_at as an ISO timestamp, total_inr as a plain number")],
                clean=clean, cleaning=["parsed platform_craftbazaar's 'Amount' text into numbers (amount_parsed)", "parsed its dd-mm-yyyy dates (date_parsed)"],
                amount=(a, "amount_parsed", "revenue on the craftbazaar platform"),
                category=(a, "Buyer City", "amount_parsed"),
                keys={a: ["Order No", "Buyer City"], b: ["order_id", "city"]},
                extra_sums=[(b, "total_inr", "shopline_total", "revenue on the shopline platform")],
                extra_tops=[(b, "city", "total_inr", "top_3_cities_on_shopline"), (a, "Item", "amount_parsed", "top_3_items_on_craftbazaar")],
                also={"column_pairs": {"Order No": "order_id", "Order Date": "created_at", "Item": "sku (by code, not name)", "Qty": "units", "Amount": "total_inr",
                                       "Buyer City": "city", "Status": "fulfilment_status"},
                      "no_shared_key_between_the_two": True,
                      "craftbazaar_rows": 520, "shopline_rows": 440,
                      "combined_total": lambda cl: num(cl[a]["amount_parsed"].sum() + cl[b]["total_inr"].sum()),
                      "combined_rows": lambda cl: int(len(cl[a]) + len(cl[b])),
                      "craftbazaar_total": num(a_total), "shopline_total_check": num(b_total)})


def m05() -> dict[str, Any]:
    r, _ = rng_for(35)
    cat = CATALOGUE["bakery"]
    sales = []
    for n in range(1, 881):
        item, price, cost, group, code = r.choice(cat)
        q = r.choice([1, 1, 2, 3, 5])
        sales.append({"order_id": f"MS{n:05d}", "order_date": (dt.date(2026, 3, 1) + dt.timedelta(days=r.randint(0, 200))).isoformat(),
                      "item_code": code, "item": item, "category": group, "quantity": q, "unit_price": price, "amount": round(price * q, 2),
                      "outlet": r.choice(["FC Road", "Baner"])})
    costs = [{"item_code": p[4], "item": p[0], "cost_per_unit": p[2], "supplier": r.choice(["Shree Flour Mills", "Dairy Best", "Sweet Supplies Co"]),
              "updated_on": "2026-02-28"} for p in cat if p[4] != "BAK-PIN1"]                 # the pineapple pastry has no cost row
    s, k = "sales.csv", "product_costs.csv"

    def clean(c: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        d = c[s].merge(c[k][["item_code", "cost_per_unit"]], on="item_code", how="left")
        d["cost_amount"] = d["cost_per_unit"] * d["quantity"]
        return {s: d, k: c[k]}

    return spec(id="M05", dir="M05_bakery_sales_and_costs", business="bakery", kind="connected (2 files, margin)",
                tests="margin that is only possible by joining: the sales file has no cost, the cost file has no sales, and one sold item has no cost row",
                app_should="join on item_code to compute margin, and say that one item's margin is unknown instead of treating its cost as zero",
                files=[File(s, df=pd.DataFrame(sales), note="no cost column; item_code is the link"),
                       File(k, df=pd.DataFrame(costs), note="11 of the 12 sold items; BAK-PIN1 (Pineapple Pastry) is missing on purpose")],
                clean=clean, cleaning=["joined product_costs onto sales by item_code and computed cost_amount = cost_per_unit x quantity (NaN where there is no cost row)"],
                amount=(s, "amount", "revenue"), category=(s, "category", "amount"), keys={s: ["order_id", "item_code"], k: ["item_code", "supplier"]},
                joins=[{"left": s, "right": k, "on": "item_code", "note": "sales -> product costs; BAK-PIN1 has no cost row"}],
                also={"item_with_no_cost_row": "BAK-PIN1",
                      "sales_rows_with_no_cost": lambda cl: int(cl[s]["cost_per_unit"].isna().sum()),
                      "revenue_with_no_cost": lambda cl: num(cl[s].loc[cl[s]["cost_per_unit"].isna(), "amount"].sum()),
                      "cost_of_goods_sold_where_known": lambda cl: num(cl[s]["cost_amount"].sum()),
                      "gross_profit_where_cost_is_known": lambda cl: num(cl[s].loc[cl[s]["cost_amount"].notna(), "amount"].sum() - cl[s]["cost_amount"].sum()),
                      "margin_pct_where_cost_is_known": lambda cl: num(100 * (1 - cl[s]["cost_amount"].sum() / cl[s].loc[cl[s]["cost_amount"].notna(), "amount"].sum()))})


def m06() -> dict[str, Any]:
    r, _ = rng_for(36)
    cat = CATALOGUE["kirana store"]
    inventory = [{"item_code": p[4], "item": p[0], "category": p[3], "qty_on_hand": r.randint(0, 80), "reorder_level": r.choice([5, 10, 15]),
                  "unit": r.choice(["pcs", "kg", "pack"])} for p in cat]
    purchases = []
    for n in range(1, 421):
        p = r.choice(cat)
        q = r.randint(5, 60)
        purchases.append({"purchase_id": f"KP-{5000 + n}", "purchase_date": (dt.date(2026, 3, 1) + dt.timedelta(days=r.randint(0, 190))).isoformat(),
                          "item_code": p[4], "supplier": r.choice(["Shree Distributors", "Pune Wholesale", "Metro Cash"]), "qty_purchased": q,
                          "unit_cost": p[2], "total_cost": round(p[2] * q, 2), "payment_status": r.choices(["Paid", "Pending"], [8, 2])[0]})
    ghost = "KIR-XTR9"                                                          # sold, but never in the inventory or purchase files
    sales = []
    for n in range(1, 1101):
        p = r.choice(cat)
        code, price = (ghost, 75) if n % 110 == 0 else (p[4], p[1])
        q = r.choice([1, 1, 2, 3, 5])
        sales.append({"bill_no": 60000 + n, "bill_date": (dt.date(2026, 3, 5) + dt.timedelta(days=r.randint(0, 190))).isoformat(),
                      "item_code": code, "qty_sold": q, "rate": price, "amount": round(price * q, 2),
                      "payment_mode": r.choice(["Cash", "UPI", "Card"])})
    pu, iv, sa = "purchases.csv", "inventory.csv", "sales.csv"
    return spec(id="M06", dir="M06_kirana_purchases_inventory_sales", business="kirana store", kind="connected (3 files, one business)",
                tests="the three sides of one shop joined on item_code: what was bought, what is in stock, and what was sold, with one item sold that neither of the other two files knows",
                app_should="link all three on item_code, compare bought against sold per item, and name the item code that appears only in sales",
                files=[File(pu, df=pd.DataFrame(purchases), note="what the shop bought, with cost"),
                       File(iv, df=pd.DataFrame(inventory), note="the item master and current stock, 12 items"),
                       File(sa, df=pd.DataFrame(sales), note=f"10 sales rows use item_code {ghost}, which is in neither other file")],
                amount=(sa, "amount", "revenue"), keys={pu: ["purchase_id", "item_code", "supplier"], iv: ["item_code", "category"], sa: ["bill_no", "item_code"]},
                extra_sums=[(pu, "total_cost", "total_purchase_cost", "money spent buying stock"), (sa, "qty_sold", "total_units_sold", "units sold"),
                            (pu, "qty_purchased", "total_units_purchased", "units bought"), (iv, "qty_on_hand", "total_units_in_stock", "units on hand")],
                joins=[{"left": sa, "right": iv, "on": "item_code", "note": f"sales -> inventory; {ghost} is the orphan"},
                       {"left": pu, "right": iv, "on": "item_code", "note": "purchases -> inventory"}],
                also={"item_code_only_in_sales": ghost,
                      "sales_rows_with_unknown_item": lambda cl: int((cl[sa]["item_code"] == ghost).sum()),
                      "revenue_of_unknown_item": lambda cl: num(cl[sa].loc[cl[sa]["item_code"] == ghost, "amount"].sum()),
                      "units_sold_vs_purchased_per_item": lambda cl: {code: {"purchased": int(cl[pu].loc[cl[pu]["item_code"] == code, "qty_purchased"].sum()),
                                                                             "sold": int(cl[sa].loc[cl[sa]["item_code"] == code, "qty_sold"].sum())}
                                                                      for code in sorted(cl[iv]["item_code"])}})


def m07() -> dict[str, Any]:
    r, _ = rng_for(37)
    cat = CATALOGUE["home-based food seller"]
    names, frames = [], {}
    for m in range(1, 7):
        rows = []
        for n in range(1, r.randint(110, 170) + 1):
            item, price, _, group, _ = r.choice(cat)
            q = r.choice([1, 1, 2, 3, 4])
            row = {"order_id": f"FS{m:02d}{n:04d}", "order_date": dt.date(2026, m, r.randint(1, 28)).isoformat(), "item": item, "category": group,
                   "quantity": q, "unit_price": price, "amount": round(price * q, 2), "city": r.choice(CITIES[:6]), "payment_mode": r.choice(["UPI", "Cash", "Card"])}
            if m == 3:
                row["festival_pack"] = r.choice(["Yes", "No", "No"])                    # March has one extra column
            rows.append(row)
        df = pd.DataFrame(rows)
        if m == 5:
            df = df[list(reversed(df.columns))]                                         # May has the same columns in reverse order
        name = f"sales_2026_{m:02d}.csv"
        names.append(name)
        frames[name] = df
    note = {"sales_2026_03.csv": "has one extra column, festival_pack, after payment_mode", "sales_2026_05.csv": "the same nine columns as January, in reverse order"}

    def clean(c: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        return {**c, "stacked": pd.concat([c[n] for n in names], ignore_index=True, sort=False)}

    def per_file(cl: dict[str, pd.DataFrame]) -> dict[str, Any]:
        return {n: {"rows": int(len(cl[n])), "amount_total": num(cl[n]["amount"].sum()), "columns": [str(x) for x in cl[n].columns]} for n in names}

    return spec(id="M07", dir="M07_food_six_monthly_files", business="home-based food seller", kind="connected (6 files, same table)",
                tests="six monthly sales files from one shop with the same columns; March has one extra column and May has the columns in a different order",
                app_should="recognise the six files as the same table (same column names, order does not matter), stack them into one, keep the extra March column, and report a combined total equal to the sum of the six files",
                files=[File(n, df=frames[n], note=note.get(n, "the same nine columns, in the same order")) for n in names],
                clean=clean, cleaning=["stacked the six files, matching columns by name (not by position); festival_pack is empty for the five months that lack it"],
                amount=("stacked", "amount", "revenue over all six months"), category=("stacked", "category", "amount"),
                keys={"stacked": ["order_id", "item", "city"]},
                also={"per_file": per_file,
                      "sum_of_the_six_file_totals": lambda cl: num(sum(cl[n]["amount"].sum() for n in names)),
                      "sum_of_the_six_file_row_counts": lambda cl: int(sum(len(cl[n]) for n in names)),
                      "stacked_rows": lambda cl: int(len(cl["stacked"])),
                      "union_of_columns": lambda cl: [str(x) for x in cl["stacked"].columns],
                      "files_with_the_same_column_set": [n for n in names if n != "sales_2026_03.csv"],
                      "file_with_an_extra_column": "sales_2026_03.csv", "file_with_columns_in_a_different_order": "sales_2026_05.csv",
                      "total_if_only_the_first_file_is_used": lambda cl: num(cl[names[0]]["amount"].sum()),
                      "monthly_revenue": lambda cl: {n[6:13]: num(cl[n]["amount"].sum()) for n in names}})


def m08() -> dict[str, Any]:
    r, _ = rng_for(38)
    cat = CATALOGUE["handicrafts seller"]
    customers = []
    for n in range(1, 161):
        name, email, phone = person(r)
        customers.append({"Customer ID": f"H{n:04d}", "Name": name, "Email": email, "Phone": phone, "City": r.choice(CITIES),
                          "Joined On": (dt.date(2025, 1, 1) + dt.timedelta(days=r.randint(0, 600))).isoformat(), "Segment": r.choice(["Retail", "Retail", "Wholesale", "Gifting"])})
    known = [c["Customer ID"] for c in customers]
    orphans = [f"H9{i:03d}" for i in range(1, 6)]
    orders = []
    for n in range(1, 701):
        item, price, _, group, code = r.choice(cat)
        q = r.choice([1, 1, 2, 3])
        orders.append({"order_id": f"HO{n:05d}", "order_date": (dt.date(2026, 1, 15) + dt.timedelta(days=r.randint(0, 240))).isoformat(),
                       "customer_id": r.choice(orphans) if n % 50 == 0 else r.choice(known), "item": item, "category": group, "quantity": q,
                       "amount": round(price * q, 2), "channel": r.choice(["Website", "Exhibition", "Instagram"])})
    o, c = "orders.csv", "customers.xlsx"
    cdf = pd.DataFrame(customers)

    def writer(path: Path) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Customers"
        ws.append(["Kala Ghar Handicrafts"])
        ws.append(["Customer list - exported from the old billing program"])
        ws.append(["Prepared by the shop accountant."])
        ws.append([])
        ws.append(list(cdf.columns))
        for row in cdf.itertuples(index=False):
            ws.append(list(row))
        ws.append([])
        ws.append(["Total customers", len(cdf)])
        wb.properties.created = wb.properties.modified = dt.datetime(2026, 10, 1)
        wb.save(path)
        freeze_xlsx(path)

    def clean(cc: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        d = cc[c]
        return {o: cc[o], c: d[d["Customer ID"].astype(str).str.fullmatch(r"H\d{4}")].reset_index(drop=True)}

    return spec(id="M08", dir="M08_handicrafts_csv_plus_excel", business="handicrafts seller", kind="connected (2 files, mixed formats)",
                tests="a CSV orders file and an Excel customer list that starts below three note rows and ends with a total line, joined by customer id (the header is spelled differently in each file)",
                app_should="find the real header row of the Excel file, drop its footer line, connect orders.customer_id to customers.xlsx Customer ID, and report the orders whose customer is not in the list",
                files=[File(o, df=pd.DataFrame(orders), note="five customer ids used by 14 orders are not in the Excel list"),
                       File(c, writer=writer, reader="excel", options={"skiprows": 4}, note="rows 1-3 are notes, row 4 is blank, row 5 is the header, the last two rows are blank and 'Total customers'")],
                clean=clean, cleaning=["read the Excel file with skiprows=4 so row 5 is the header", "dropped the footer rows that are not a customer id"],
                amount=(o, "amount", "revenue"), category=(o, "category", "amount"),
                keys={o: ["order_id", "customer_id"], c: ["Customer ID", "City"]},
                joins=[{"left": o, "right": c, "on": "customer_id", "left_on": "customer_id", "right_on": "Customer ID", "note": "orders -> customers; the orphan ids are H9001-H9005"}],
                also={"customers_in_excel_list": lambda cl: int(len(cl[c])),
                      "orders_with_no_matching_customer": lambda cl: int(cl[o]["customer_id"].isin(orphans).sum()),
                      "orphan_customer_ids_used_by_an_order": lambda cl: sorted(set(cl[o]["customer_id"]) - set(cl[c]["Customer ID"])),
                      "revenue_of_orphan_orders": lambda cl: num(cl[o].loc[cl[o]["customer_id"].isin(orphans), "amount"].sum()),
                      "customers_who_never_ordered": lambda cl: int((~cl[c]["Customer ID"].isin(cl[o]["customer_id"])).sum())})


def m09() -> dict[str, Any]:
    r, _ = rng_for(39)
    cat = CATALOGUE["kirana store"]
    customers = [{"customer_id": f"K{n:04d}", "customer_name": person(r)[0], "city": r.choice(CITIES[:5]), "credit_account": r.choice(["Yes", "No", "No"])} for n in range(1, 121)]
    products = [{"product_id": p[4], "product_name": p[0], "category": p[3], "mrp": p[1], "purchase_rate": p[2]} for p in cat]
    known = [x["customer_id"] for x in customers]
    orphans = ["K9001", "K9002", "K9003"]
    orders = []
    for n in range(1, 501):
        item, price, _, _, code = r.choice(cat)
        q = r.choice([1, 2, 2, 3, 5])
        orders.append({"order_id": f"KO{n:05d}", "order_date": (dt.date(2026, 4, 1) + dt.timedelta(days=r.randint(0, 150))).isoformat(),
                       "customer_id": r.choice(orphans) if n % 50 == 0 else r.choice(known), "product_id": code, "quantity": q,
                       "amount": round(price * q, 2), "channel": r.choice(["Counter", "Phone", "WhatsApp"])})
    shipped = [o for i, o in enumerate(orders) if i % 7 != 3 and o["channel"] != "Counter"]      # counter sales and some others have no delivery
    shipments = [{"shipment_id": f"SH{i:05d}", "order_id": o["order_id"], "courier": r.choice(["Local Rider", "Speed Post", "App Courier"]),
                  "ship_date": o["order_date"], "delivery_days": r.randint(1, 5)} for i, o in enumerate(shipped, start=1)]
    suppliers = [{"supplier_ref": f"SUP-{i:02d}", "supplier_name": f"{r.choice(LAST)} {r.choice(['Traders', 'Distributors', 'Wholesale'])}", "city": r.choice(CITIES),
                  "payment_terms_days": r.choice([7, 15, 30])} for i in range(1, 10)]
    staff = [{"staff_no": f"EMP-{100 + i}", "staff_name": person(r)[0], "role": r.choice(["Cashier", "Packer", "Delivery", "Helper"]), "monthly_pay": r.choice([9000, 11000, 13500, 16000])} for i in range(1, 9)]
    expenses = [{"month": f"{m} 2026", "expense_type": e, "amount": r.randint(2000, 38000)} for m in ["Apr", "May", "Jun", "Jul", "Aug"] for e in ["Rent", "Electricity", "Packaging", "Fuel", "Repairs", "Phone"]]
    festivals = [{"festival": f, "festival_date": d, "expected_demand": r.choice(["High", "Medium"])} for f, d in
                 [("Ugadi", "2026-03-19"), ("Ram Navami", "2026-03-27"), ("Eid", "2026-03-21"), ("Akshaya Tritiya", "2026-04-19"), ("Buddha Purnima", "2026-05-01"),
                  ("Muharram", "2026-06-26"), ("Rath Yatra", "2026-07-16"), ("Raksha Bandhan", "2026-08-28"), ("Janmashtami", "2026-09-04"), ("Ganesh Chaturthi", "2026-09-14")]]
    o, c, p, s = "orders.csv", "customers.csv", "products.csv", "shipments.csv"
    u = ["suppliers.csv", "staff_roster.csv", "shop_expenses.csv", "festival_calendar.csv"]
    return spec(id="M09", dir="M09_kirana_eight_files", business="kirana store", kind="connected (8 files at the upload limit)",
                tests="eight files, the most one upload takes: four that connect (orders, customers, products, shipments) and four that have no link to anything (suppliers, staff, expenses, festivals)",
                app_should="accept all eight files, find the three real links (orders-customers, orders-products, shipments-orders), connect the four tables, say the other four are not connected instead of inventing links, and report the orphan customers",
                files=[File(o, df=pd.DataFrame(orders), note="three customer ids used by 10 orders are not in customers.csv"), File(c, df=pd.DataFrame(customers)),
                       File(p, df=pd.DataFrame(products), note="product_id is the item code"), File(s, df=pd.DataFrame(shipments), note="one row per delivered order; counter sales have none"),
                       File(u[0], df=pd.DataFrame(suppliers), note="unrelated: no column links to the other files"), File(u[1], df=pd.DataFrame(staff), note="unrelated"),
                       File(u[2], df=pd.DataFrame(expenses), note="unrelated; months are text"), File(u[3], df=pd.DataFrame(festivals), note="unrelated")],
                amount=(o, "amount", "revenue"), category=(o, "channel", "amount"),
                keys={o: ["order_id", "customer_id", "product_id"], c: ["customer_id"], p: ["product_id"], s: ["shipment_id", "order_id"]},
                joins=[{"left": o, "right": c, "on": "customer_id", "note": "orders -> customers; the orphan ids are K9001-K9003"},
                       {"left": o, "right": p, "on": "product_id", "note": "orders -> products"},
                       {"left": s, "right": o, "on": "order_id", "note": "shipments -> orders; every shipment belongs to an order, not every order has a shipment"}],
                also={"files_uploaded": 8, "upload_file_limit": 8, "files_that_connect": [o, c, p, s], "files_with_no_link": u, "real_links_expected": 3,
                      "orders_with_no_matching_customer": lambda cl: int(cl[o]["customer_id"].isin(orphans).sum()),
                      "orders_without_a_shipment": lambda cl: int((~cl[o]["order_id"].isin(cl[s]["order_id"])).sum()),
                      "average_delivery_days": lambda cl: num(cl[s]["delivery_days"].mean())})


# ================================================================== assembling the pack
BUILDERS = [r01, r02, r03, r04, r05, r06, r07, r08, r09, r10, r11, r12, r13, r14, r15, r16, r17, m01, m02, m03, m04, m05, m06, m07, m08, m09]

README_HEAD = """# Review pack (synthetic)

Built by `scripts/make_review_datasets.py` (deterministic, seeded {seed}). No AI, no network, no real
personal data: every name, phone number and e-mail address is made up. Each dataset is its own folder,
holds under 3,000 rows, and carries an `expected.json` with figures computed by pandas.

These are for a **real-AI review**: upload one dataset's files, let the run finish, then check the
report against `expected.json`. The `app_should` line in each file is the one thing that dataset is
there to test.

`expected.json` holds, per dataset: how to read each file (`reader`, `options`), its row and column
counts, missing values per column and exact duplicate count; then under `answers` the row count,
the main total, the top 3 of the main category, distinct key counts, missing values per column,
duplicates, and for connected sets the joined row counts and the unmatched ids. Where a file is
dirty, `cleaning_applied` says exactly what cleaning the figures assume. Where a file is genuinely
ambiguous (R07), `ambiguities` lists each reading and the number it gives, because there is no single
right answer and the app is expected to ask.

| id | business | files | rows | what it tests | the app should |
|---|---|---|---|---|---|
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    lines, index = [], []
    for fn in BUILDERS:
        s = fn()
        out = build(s)
        rows = sum(f["rows"] for f in out["files"].values())
        names = ", ".join(f"`{n}`" for n in out["files"])
        lines.append(f"| **{out['id']}** | {out['business_type']} | {names} | {rows} | {out['what_it_tests']} | {out['app_should']} |")
        index.append({"id": out["id"], "folder": s["dir"], "business_type": out["business_type"], "kind": out["kind"],
                      "files": list(out["files"]), "rows": rows, "what_it_tests": out["what_it_tests"], "app_should": out["app_should"]})
        print(f"{out['id']}  {s['dir']:<42} {len(out['files'])} file(s)  {rows:>5} rows")
    (OUT / "README.md").write_text(README_HEAD.format(seed=SEED) + "\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "index.json").write_text(json.dumps({"generated_by": "scripts/make_review_datasets.py", "seed": SEED, "datasets": index},
                                               indent=2, ensure_ascii=False, default=jsonable), encoding="utf-8")
    print(f"\n{len(index)} datasets, {sum(d['rows'] for d in index)} rows in all -> {OUT}")


if __name__ == "__main__":
    main()
