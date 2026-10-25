"""Benchmark #3: clothing mall. Seeded and deterministic; stdlib only.

Writes sample_data/mall/{sales.csv, truth.json, ANSWER_KEY.md}.
Truth is computed on the CLEAN data (after de-duplication, before blanking Qty
and before the messy formatting), so the key states what a correct analysis
should recover once the mess is cleaned.
"""
import calendar
import csv
import json
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

SEED = 20240611
OUT = Path(__file__).resolve().parent.parent / "sample_data" / "mall"
N_CLEAN = 39970          # + 30 duplicates = 40,000 rows in the file
N_DUPES = 30

STORES = ["Phoenix Mall", "Orion Square", "Lakeside Outlet", "Central Plaza", "Harbor Point", "Airport Kiosk"]
STORE_W = [
    [0.28, 0.20, 0.20, 0.14, 0.10, 0.08],   # year 1 (Apr 2023 - Mar 2024)
    [0.30, 0.22, 0.09, 0.16, 0.12, 0.11],   # year 2 (Apr 2024 - Mar 2025): Lakeside shrinks
]
TOP_STORE, SHRINK_STORE = "Phoenix Mall", "Lakeside Outlet"

DEPTS = ["Menswear", "Womenswear", "Kidswear", "Footwear", "Accessories"]
DEPT_W = [0.44, 0.26, 0.12, 0.10, 0.08]
DEPT_MRP = {"Menswear": (899, 2999), "Womenswear": (799, 2799), "Kidswear": (499, 1499),
            "Footwear": (999, 3499), "Accessories": (299, 1199)}
BRANDS = {
    "Menswear": [("Raymond", .25), ("Van Heusen", .22), ("Peter England", .20), ("Allen Solly", .18), ("Levis", .15)],
    "Womenswear": [("Biba", .30), ("W", .25), ("Allen Solly", .15), ("Urban Weave", .30)],
    "Kidswear": [("Levis", .30), ("Little Stars", .40), ("Tiny Tots", .30)],
    "Footwear": [("Stride", .50), ("Metro Steps", .50)],
    "Accessories": [("Leather Co", .50), ("Allen Solly", .50)],
}
RETURN_BRAND, RETURN_RATE, BASE_RETURN = "Urban Weave", 0.38, 0.05
PAY = [("Cash", .20), ("UPI", .40), ("Card", .35), ("Wallet", .05)]
SIZES = ["S", "M", "L", "XL", "XXL"]
SEASON = {4: 1.0, 5: 1.0, 6: 0.55, 7: 0.85, 8: 0.95, 9: 1.0, 10: 1.9, 11: 1.7, 12: 1.15, 1: 0.95, 2: 0.9, 3: 1.0}
FESTIVE, WEAK = (10, 11), 6

COLS = ["Bill No", "Bill Date", "Store", "Brand", "Dept", "SKU", "Size", "Qty", "MRP",
        "Sale Price", "Discount %", "Pay Mode", "Return Flag"]


def wchoice(rng, pairs):
    return rng.choices([p[0] for p in pairs], [p[1] for p in pairs])[0]


def build_catalog(rng):
    cat = {}
    for dept in DEPTS:
        lo, hi = DEPT_MRP[dept]
        for brand, _ in BRANDS[dept]:
            cat[(dept, brand)] = [
                (f"{brand[:3].upper()}-{dept[:3].upper()}-{i:02d}", round(rng.uniform(lo, hi) / 50) * 50 - 1)
                for i in range(1, 9)]
    return cat


def months():
    out = []
    for i in range(24):
        y = 2023 + (3 + i) // 12
        m = (3 + i) % 12 + 1
        out.append((y, m))
    return out


def discount_mean(mi):
    return 14.0 if mi < 12 else 14.0 + (mi - 12) * (18.0 / 11.0)


def generate(rng):
    cat = build_catalog(rng)
    ms = months()
    mw = [SEASON[m] * (1.0 if i < 12 else 1.25) for i, (_, m) in enumerate(ms)]
    bills = []
    n = 0
    while n < N_CLEAN:
        mi = rng.choices(range(24), mw)[0]
        y, m = ms[mi]
        d = date(y, m, rng.randint(1, calendar.monthrange(y, m)[1]))
        store = rng.choices(STORES, STORE_W[mi // 12])[0]
        pay = wchoice(rng, PAY)
        lines = []
        for _ in range(rng.choices([1, 2, 3], [.55, .30, .15])[0]):
            if n >= N_CLEAN:
                break
            dept = rng.choices(DEPTS, DEPT_W)[0]
            brand = wchoice(rng, BRANDS[dept])
            sku, mrp = rng.choice(cat[(dept, brand)])
            disc = min(60, max(0, round(rng.gauss(discount_mean(mi), 3))))
            sale = round(mrp * (1 - disc / 100))
            rate = RETURN_RATE if brand == RETURN_BRAND else BASE_RETURN
            lines.append(dict(Store=store, Brand=brand, Dept=dept, SKU=sku, Size=rng.choice(SIZES),
                              Qty=rng.choices([1, 2, 3, 4], [.70, .20, .07, .03])[0], MRP=mrp,
                              Sale=sale, Disc=disc, Pay=pay, Ret="Y" if rng.random() < rate else "N", Date=d, mi=mi))
            n += 1
        bills.extend(lines and [lines])
    bills.sort(key=lambda b: b[0]["Date"])
    rows = []
    for bi, b in enumerate(bills, 1):
        for r in b:
            r["Bill"] = f"B{bi:06d}"
            rows.append(r)
    return rows


def rev(r):
    return r["Qty"] * r["Sale"]


def compute_truth(rows):
    t = {"clean_rows": len(rows), "currency": "INR", "period": "2023-04-01 to 2025-03-31"}
    yr = lambda r: r["mi"] // 12
    for label, keep in (("gross", lambda r: True), ("net_of_returns", lambda r: r["Ret"] == "N")):
        st = defaultdict(lambda: [0, 0])
        for r in rows:
            if keep(r):
                st[r["Store"]][yr(r)] += rev(r)
        t[f"store_revenue_{label}"] = {s: {"year1": v[0], "year2": v[1],
                                           "yoy_pct": round((v[1] / v[0] - 1) * 100, 1)} for s, v in st.items()}
    sg = t["store_revenue_gross"]
    t["top_revenue_store"] = max(sg, key=lambda s: sg[s]["year1"] + sg[s]["year2"])
    t["shrinking_store"] = min(sg, key=lambda s: sg[s]["yoy_pct"])
    assert t["top_revenue_store"] == TOP_STORE and t["shrinking_store"] == SHRINK_STORE
    for lab in ("store_revenue_gross", "store_revenue_net_of_returns"):
        d = t[lab]
        assert max(d, key=lambda s: d[s]["year1"] + d[s]["year2"]) == TOP_STORE
        assert [s for s in d if d[s]["yoy_pct"] < 0] == [SHRINK_STORE], lab

    br = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        b = br[r["Brand"]]
        b[0] += 1
        b[1] += r["Ret"] == "Y"
        b[2] += rev(r)
    t["brand_return_rate_pct"] = {k: round(v[1] / v[0] * 100, 1) for k, v in sorted(br.items())}
    t["high_return_brand"] = max(t["brand_return_rate_pct"], key=t["brand_return_rate_pct"].get)
    others = [v for k, v in t["brand_return_rate_pct"].items() if k != RETURN_BRAND]
    assert t["high_return_brand"] == RETURN_BRAND and max(others) < 8

    half = defaultdict(lambda: [0, 0, 0.0, 0])
    for r in rows:
        h = half[r["mi"] // 6]
        h[0] += rev(r)
        h[1] += r["Qty"]
        h[2] += r["Disc"]
        h[3] += 1
    t["discount_and_revenue_per_unit_by_half_year"] = {
        f"H{k + 1}": {"avg_discount_pct": round(v[2] / v[3], 1), "revenue_per_unit": round(v[0] / v[1], 1)}
        for k, v in sorted(half.items())}
    hs = t["discount_and_revenue_per_unit_by_half_year"]
    assert hs["H4"]["avg_discount_pct"] > hs["H3"]["avg_discount_pct"] > hs["H2"]["avg_discount_pct"] - 1
    assert hs["H4"]["revenue_per_unit"] < hs["H1"]["revenue_per_unit"] * 0.92

    mo = defaultdict(int)
    cal = defaultdict(int)
    for r in rows:
        mo[r["Date"].strftime("%Y-%m")] += rev(r)
        cal[r["Date"].month] += rev(r)
    t["monthly_revenue"] = dict(sorted(mo.items()))
    ranked = sorted(cal, key=cal.get, reverse=True)
    t["festive_peak_months"] = [calendar.month_name[m] for m in FESTIVE]
    t["weak_month"] = calendar.month_name[WEAK]
    assert set(ranked[:2]) == set(FESTIVE) and ranked[-1] == WEAK
    t["revenue_by_calendar_month"] = {calendar.month_name[m]: cal[m] for m in sorted(cal)}
    tot = sum(rev(r) for r in rows)
    t["total_gross_revenue"] = tot
    dr = defaultdict(int)
    for r in rows:
        dr[r["Dept"]] += rev(r)
    t["dept_revenue_share_pct"] = {k: round(v / tot * 100, 1) for k, v in sorted(dr.items(), key=lambda x: -x[1])}
    t["dominant_dept"] = next(iter(t["dept_revenue_share_pct"]))
    assert t["dept_revenue_share_pct"]["Menswear"] > 40

    def spread(key):
        a = defaultdict(lambda: [0, 0])
        for r in rows:
            a[r[key]][0] += rev(r)
            a[r[key]][1] += 1
        per = {k: v[0] / v[1] for k, v in a.items()}
        return {"avg_revenue_per_line": {k: round(v, 1) for k, v in sorted(per.items())},
                "max_vs_min_pct": round((max(per.values()) / min(per.values()) - 1) * 100, 1)}
    t["decoys"] = {"Pay Mode": spread("Pay"), "Size": spread("Size")}
    assert all(d["max_vs_min_pct"] < 6 for d in t["decoys"].values()), t["decoys"]
    t["not_computable"] = ["margin", "profit", "cost", "markup"]
    t["planted_mess"] = {
        "date_formats": ["YYYY-MM-DD", "DD/MM/YYYY", "DD-Mon-YYYY"],
        "sale_price_rs_prefix_pct": 5, "blank_qty_pct": 1, "exact_duplicate_rows": N_DUPES,
        "brand_variants": ["Raymond", "raymond "]}
    return t


def messy(rng, rows):
    out = []
    for r in rows:
        d = r["Date"]
        f = rng.choices(range(3), [.5, .3, .2])[0]
        ds = (d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%d-%b-%Y"))[f]
        sale = f"{r['Sale']:,}"
        sale = f"Rs. {sale}" if rng.random() < 0.05 else str(r["Sale"])
        brand = r["Brand"]
        if brand == "Raymond" and rng.random() < 0.12:
            brand = "raymond "
        qty = "" if rng.random() < 0.01 else r["Qty"]
        out.append([r["Bill"], ds, r["Store"], brand, r["Dept"], r["SKU"], r["Size"], qty,
                    r["MRP"], sale, r["Disc"], r["Pay"], r["Ret"]])
    for i in sorted(rng.sample(range(len(out)), N_DUPES), reverse=True):
        out.insert(i + 1, list(out[i]))
    return out


def answer_key(t):
    sg, hs = t["store_revenue_gross"], t["discount_and_revenue_per_unit_by_half_year"]
    sh = SHRINK_STORE
    lines = [
        "# Benchmark #3: Clothing Mall - Answer Key",
        "",
        "One flat file, `sales.csv` (40,000 rows incl. 30 duplicates, Apr 2023 - Mar 2025, INR). One row = one bill line. "
        "Figures below are on clean data (duplicates dropped); blank-Qty rows (~1%) make a real run differ by up to ~1%.",
        "",
        "## Should-ask questions (any 3+ is good; each is a genuine ambiguity)",
        "1. Is **revenue = Qty x Sale Price**, and is Sale Price per unit or the line total?",
        "2. Should **returned lines (Return Flag = Y)** be netted out of revenue, or reported gross?",
        "3. How to treat the **~1% blank Qty** rows: drop, assume 1, or impute?",
        "4. Slash dates: **DD/MM/YYYY or MM/DD/YYYY**? (Data is Indian; day-first is the safe reading.)",
        "5. Is there a **cost / margin** source? (None in the file - the right behaviour is to say margin cannot be computed.)",
        "6. Confirm the 30 exact **duplicate lines** are to be dropped, not genuine repeat sales.",
        "7. Which period is the comparison base for 'year on year' (FY Apr-Mar vs calendar year)?",
        "",
        "## Must-not-ask questions (answerable from the data or trivially safe)",
        "- Which file / which sheet to use (there is exactly one).",
        "- What `Qty`, `MRP`, `Store`, `Dept`, `Size`, `Bill Date` mean.",
        "- Whether `Raymond` and `raymond ` are the same brand (obviously; trim + case-fold).",
        "- Whether to strip the `Rs.` prefix and commas from Sale Price (obviously yes).",
        "- How Discount % is defined (verifiable: (MRP - Sale Price) / MRP).",
        "- Asking the user to supply a cost column or join table (no second table exists).",
        "- Asking the user to name which stores / brands matter before looking.",
        "",
        "## Expected findings",
        f"1. **Top store: {t['top_revenue_store']}** - highest revenue in both years and on gross or net basis.",
        f"2. **Shrinking store: {sh}** - revenue {sg[sh]['year1']:,} -> {sg[sh]['year2']:,} ({sg[sh]['yoy_pct']}% YoY). "
        "It is the only store that declines; the others are flat or up. Units fall too, so it is not a price effect only.",
        f"3. **High-return brand: {t['high_return_brand']}** - {t['brand_return_rate_pct'][RETURN_BRAND]}% of lines returned "
        f"vs at most {max(v for k, v in t['brand_return_rate_pct'].items() if k != RETURN_BRAND)}% for every other brand.",
        f"4. **Discount deepening, revenue per unit falling in year 2** - avg discount {hs['H1']['avg_discount_pct']}% (H1) / "
        f"{hs['H2']['avg_discount_pct']}% (H2) -> {hs['H3']['avg_discount_pct']}% (H3) / {hs['H4']['avg_discount_pct']}% (H4); "
        f"revenue per unit {hs['H1']['revenue_per_unit']:,} -> {hs['H4']['revenue_per_unit']:,}. "
        "Volume does not rise to compensate, so deeper discounting is not buying growth.",
        f"5. **Festive peak: {' and '.join(t['festive_peak_months'])}** (top two calendar months in both years); "
        f"**weak month: {t['weak_month']}**.",
        f"6. **Dominant dept: {t['dominant_dept']}** - {t['dept_revenue_share_pct'][t['dominant_dept']]}% of revenue "
        f"(next: {list(t['dept_revenue_share_pct'])[1]} {list(t['dept_revenue_share_pct'].values())[1]}%).",
        "",
        "### Decoys - must NOT be reported as effects",
        f"- **Pay Mode vs revenue**: avg revenue per line differs by only {t['decoys']['Pay Mode']['max_vs_min_pct']}% across modes (random).",
        f"- **Size vs revenue**: differs by only {t['decoys']['Size']['max_vs_min_pct']}% across sizes (random).",
        "A correct answer says 'no meaningful relationship' or leaves them out; calling either a driver is a failure.",
        "",
        "### Data-quality items to detect and handle",
        "3 date formats (YYYY-MM-DD, DD/MM/YYYY, DD-Mon-YYYY); `Rs.` prefix (with commas) on ~5% of Sale Price; "
        "~1% blank Qty; 30 exact duplicate rows; `Raymond` / `raymond ` variants.",
        "",
        "## Red flags (automatic deductions)",
        "- **Inventing a margin / profit / markup.** No cost column exists; MRP - Sale Price is discount, not margin.",
        "- **Treating `Bill No` as a measure** (summing or averaging it). It is an identifier; count distinct for bills.",
        "- Treating `Discount %` as additive, or averaging it unweighted when a revenue-weighted figure is needed without saying so.",
        "- Summing `Sale Price` or `MRP` instead of Qty x Sale Price.",
        "- Parsing slash dates month-first, or silently dropping rows with unparseable dates / Rs. prices.",
        "- Not de-duplicating, so every figure is overstated; or counting `Raymond` and `raymond ` as two brands.",
        "- Calling Pay Mode or Size a driver of revenue.",
        "- Blaming the shrinking store on pricing without checking units, or ignoring returns when ranking brands.",
        "- Reporting a trend for the whole business from only one year, or missing that year 2 differs from year 1.",
        "",
        "## Scoring hint",
        "Findings 1-6: one point each (finding + a correct number). Decoys: one point for correctly declining both. "
        "Cleaning: one point for handling all five mess items. Questions: up to two points for relevant should-asks, "
        "minus one per must-ask. Any red flag: minus two each.",
        "",
    ]
    return "\n".join(lines)


def main():
    rng = random.Random(SEED)
    rows = generate(rng)
    truth = compute_truth(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "sales.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(messy(rng, rows))
    (OUT / "truth.json").write_text(json.dumps(truth, indent=2, default=str), encoding="utf-8")
    (OUT / "ANSWER_KEY.md").write_text(answer_key(truth), encoding="utf-8")
    print(f"wrote {OUT} ({truth['clean_rows'] + N_DUPES} rows)")


if __name__ == "__main__":
    main()
