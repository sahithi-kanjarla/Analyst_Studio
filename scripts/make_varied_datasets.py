"""Generates nine synthetic sales exports into backend/tests/fixtures/varied/, modelled on real export styles. Deterministic (seeded), no real personal data: every name, e-mail, phone number and GSTIN is made up.

    python scripts/make_varied_datasets.py            # writes the nine files and the README

Each file's README line says whether its layout is "documented" (taken from the platform's public documentation: the column names and the way rows are laid out) or "typical pattern"
(an approximation of what such an export looks like in practice, not a copy of any vendor's file)."""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "varied"
SEED = 20240601
FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Sana", "Vihaan", "Isha", "Arjun", "Nisha", "Dev", "Kiara", "Rahul", "Anaya", "Imran", "Pooja", "Karan", "Tara", "Yash", "Zoya"]
LAST = ["Sharma", "Verma", "Iyer", "Khan", "Patel", "Reddy", "Nair", "Singh", "Das", "Mehta", "Joshi", "Rao", "Gupta", "Bose", "Menon"]
CITIES = [("Mumbai", "Maharashtra", "400001"), ("Pune", "Maharashtra", "411001"), ("Delhi", "Delhi", "110001"), ("Bengaluru", "Karnataka", "560001"), ("Chennai", "Tamil Nadu", "600001"),
          ("Kolkata", "West Bengal", "700001"), ("Hyderabad", "Telangana", "500001"), ("Jaipur", "Rajasthan", "302001"), ("Ahmedabad", "Gujarat", "380001")]
PRODUCTS = [("Cotton Kurta", 799, "KUR-01"), ("Denim Jacket", 2199, "JAC-02"), ("Leather Wallet", 649, "WAL-03"), ("Running Shoes", 2999, "SHO-04"), ("Steel Bottle", 399, "BOT-05"),
            ("Silk Scarf", 1299, "SCA-06"), ("Yoga Mat", 899, "MAT-07"), ("Desk Lamp", 1499, "LAM-08"), ("Backpack", 1899, "BAG-09"), ("Wireless Earbuds", 2499, "EAR-10")]


def rng_for(n: int):
    return random.Random(SEED + n), np.random.RandomState(SEED + n)


def person(r: random.Random) -> tuple[str, str, str]:
    f, l = r.choice(FIRST), r.choice(LAST)
    return f"{f} {l}", f"{f.lower()}.{l.lower()}{r.randint(1, 99)}@example.com", f"{r.choice('6789')}{r.randint(10 ** 8, 10 ** 9 - 1)}"


def gstin(r: random.Random) -> str:
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return f"{r.randint(1, 36):02d}{''.join(r.choice(letters) for _ in range(5))}{r.randint(1000, 9999)}{r.choice(letters)}{r.randint(1, 9)}Z{r.choice('0123456789ABCDEFGH')}"


# ------------------------------------------------------------------ (a) Shopify-style orders export: one row per line item                                           [documented]
def shopify() -> None:
    r, _ = rng_for(1)
    cols = ["Name", "Email", "Financial Status", "Paid at", "Fulfillment Status", "Fulfilled at", "Accepts Marketing", "Currency", "Subtotal", "Shipping", "Taxes", "Total", "Discount Code", "Discount Amount",
            "Shipping Method", "Created at", "Lineitem quantity", "Lineitem name", "Lineitem price", "Lineitem compare at price", "Lineitem sku", "Lineitem requires shipping", "Lineitem taxable",
            "Lineitem fulfillment status", "Billing Name", "Billing Street", "Billing Address1", "Billing Address2", "Billing Company", "Billing City", "Billing Zip", "Billing Province", "Billing Country",
            "Billing Phone", "Shipping Name", "Shipping Street", "Shipping Address1", "Shipping Address2", "Shipping Company", "Shipping City", "Shipping Zip", "Shipping Province", "Shipping Country",
            "Shipping Phone", "Notes", "Note Attributes", "Cancelled at", "Payment Method", "Payment Reference", "Refunded Amount", "Vendor", "Id", "Tags", "Risk Level", "Source", "Receipt Number", "Phone"]
    rows, base = [], dt.datetime(2024, 1, 1, 9, 0, 0)
    for n in range(1, 321):
        name, email, phone = person(r)
        city, prov, zipc = r.choice(CITIES)
        created = base + dt.timedelta(minutes=r.randint(0, 60 * 24 * 150))
        stamp = created.strftime("%Y-%m-%d %H:%M:%S +0530")
        items = [r.choice(PRODUCTS) for _ in range(r.choice([1, 1, 1, 2, 2, 3, 4]))]
        qtys = [r.choice([1, 1, 1, 2, 3]) for _ in items]
        subtotal = sum(p[1] * q for p, q in zip(items, qtys))
        discount = round(subtotal * 0.1, 2) if r.random() < 0.2 else 0.0
        ship = 0.0 if subtotal > 1500 else 60.0
        tax = round((subtotal - discount) * 0.05, 2)
        total = round(subtotal - discount + ship + tax, 2)
        status = r.choices(["paid", "pending", "refunded", "partially_refunded"], [80, 8, 7, 5])[0]
        cancelled = stamp if r.random() < 0.03 else ""
        order = {"Name": f"#{1000 + n}", "Email": email, "Financial Status": status, "Paid at": stamp if status != "pending" else "", "Fulfillment Status": r.choice(["fulfilled", "fulfilled", "unfulfilled", "partial"]),
                 "Fulfilled at": stamp, "Accepts Marketing": r.choice(["yes", "no"]), "Currency": "INR", "Subtotal": subtotal, "Shipping": ship, "Taxes": tax, "Total": total,
                 "Discount Code": "WELCOME10" if discount else "", "Discount Amount": discount, "Shipping Method": r.choice(["Standard", "Express"]), "Created at": stamp,
                 "Billing Name": name, "Billing Street": f"{r.randint(1, 99)} MG Road", "Billing Address1": f"{r.randint(1, 99)} MG Road", "Billing City": city, "Billing Zip": f"'{zipc}", "Billing Province": prov,
                 "Billing Country": "IN", "Billing Phone": phone, "Shipping Name": name, "Shipping Street": f"{r.randint(1, 99)} MG Road", "Shipping Address1": f"{r.randint(1, 99)} MG Road", "Shipping City": city,
                 "Shipping Zip": f"'{zipc}", "Shipping Province": prov, "Shipping Country": "IN", "Shipping Phone": phone, "Notes": "", "Cancelled at": cancelled, "Payment Method": r.choice(["UPI", "Card", "COD"]),
                 "Payment Reference": f"PAY{r.randint(10 ** 7, 10 ** 8)}", "Refunded Amount": round(total * (1 if status == "refunded" else 0.3 if status == "partially_refunded" else 0), 2), "Vendor": "Demo Store",
                 "Id": 5_000_000_000 + n, "Tags": r.choice(["", "vip", "wholesale"]), "Risk Level": r.choice(["Low", "Low", "Medium"]), "Source": r.choice(["web", "pos", "instagram"]), "Receipt Number": 1000 + n, "Phone": phone}
        for k, (p, q) in enumerate(zip(items, qtys)):
            line = {"Lineitem quantity": q, "Lineitem name": p[0], "Lineitem price": p[1], "Lineitem compare at price": "", "Lineitem sku": p[2], "Lineitem requires shipping": "true", "Lineitem taxable": "true",
                    "Lineitem fulfillment status": r.choice(["fulfilled", "pending"])}
            rows.append({**order, **line} if k == 0 else {"Name": order["Name"], "Email": email, **line})          # extra line items repeat only the name, the e-mail and the line item fields
    pd.DataFrame(rows, columns=cols).to_csv(OUT / "a_shopify_orders.csv", index=False, encoding="utf-8")


# ------------------------------------------------------------------ (b) Flipkart-style payout workbook: several sheets, fees with waivers, returns, cancellations                [documented]
def flipkart() -> None:
    r, _ = rng_for(2)
    orders, returns, cancels = [], [], []
    base = dt.date(2024, 2, 1)
    for n in range(1, 261):
        sku = r.choice(PRODUCTS)
        qty = r.choice([1, 1, 2])
        sale = sku[1] * qty
        od = base + dt.timedelta(days=r.randint(0, 120))
        commission = round(sale * 0.12, 2)
        collection = round(sale * 0.02, 2)
        fixed = 20.0
        shipping = round(30 + 10 * qty, 2)
        waive = lambda fee: round(fee, 2) if r.random() < 0.25 else 0.0
        w = {"Commission Fee Waiver": waive(commission), "Collection Fee Waiver": waive(collection), "Fixed Fee Waiver": waive(fixed), "Shipping Fee Waiver": waive(shipping)}
        net = round(sale - commission - collection - fixed - shipping + sum(w.values()), 2)
        status = r.choices(["Delivered", "Returned", "Cancelled"], [80, 12, 8])[0]
        oid = f"OD{r.randint(10 ** 17, 10 ** 18 - 1)}"
        orders.append({"Order ID": oid, "Order Item ID": f"{oid}-1", "SKU": sku[2], "Product": sku[0], "Order Date": od.strftime("%d-%m-%Y"), "Dispatch Date": (od + dt.timedelta(days=2)).strftime("%d-%m-%Y"),
                       "Settlement Date": (od + dt.timedelta(days=9)).strftime("%d-%m-%Y"), "Order Item Status": status, "Quantity": qty, "Sale Amount": sale, "Commission Fee": commission,
                       "Commission Fee Waiver": w["Commission Fee Waiver"], "Collection Fee": collection, "Collection Fee Waiver": w["Collection Fee Waiver"], "Fixed Fee": fixed, "Fixed Fee Waiver": w["Fixed Fee Waiver"],
                       "Shipping Fee": shipping, "Shipping Fee Waiver": w["Shipping Fee Waiver"], "Net Settlement": net if status == "Delivered" else 0.0})
        if status == "Returned":
            returns.append({"Return ID": f"RET{r.randint(10 ** 8, 10 ** 9)}", "Order ID": oid, "Return Date": (od + dt.timedelta(days=12)).strftime("%d-%m-%Y"), "Return Reason": r.choice(["Size issue", "Damaged", "Not as described"]),
                            "Refund Amount": sale})
        if status == "Cancelled":
            cancels.append({"Order ID": oid, "Cancel Date": (od + dt.timedelta(days=1)).strftime("%d-%m-%Y"), "Cancelled By": r.choice(["Buyer", "Seller"]), "Reason": r.choice(["Out of stock", "Changed mind"])})
    help_rows = [["How to read this workbook"], ["Orders: one row per order item. Each fee has a matching waiver column; the waiver is added back to the settlement."], ["Returns: items sent back, with the amount refunded."],
                 ["Cancellations: orders cancelled before dispatch."], ["Net Settlement = Sale Amount - fees + waivers (0 for items that were not delivered)."]]
    with pd.ExcelWriter(OUT / "b_flipkart_payout.xlsx", engine="openpyxl") as xw:
        xw.book.properties.created = xw.book.properties.modified = dt.datetime(2024, 6, 1)          # so the file is the same every time
        pd.DataFrame(help_rows).to_excel(xw, sheet_name="Help", index=False, header=False)
        pd.DataFrame(orders).to_excel(xw, sheet_name="Orders", index=False)
        pd.DataFrame(returns).to_excel(xw, sheet_name="Returns", index=False)
        pd.DataFrame(cancels).to_excel(xw, sheet_name="Cancellations", index=False)


# ------------------------------------------------------------------ (c) Amazon-style settlement flat file: tab-delimited, a summary row first, fees and refunds as negative rows           [typical pattern]
def amazon() -> None:
    r, _ = rng_for(3)
    head = ["settlement-id", "settlement-start-date", "settlement-end-date", "deposit-date", "total-amount", "currency", "transaction-type", "order-id", "merchant-order-id", "adjustment-id", "shipment-id",
            "marketplace-name", "amount-type", "amount-description", "amount", "fulfillment-id", "posted-date", "posted-date-time", "order-item-code", "merchant-order-item-id", "merchant-adjustment-item-id",
            "sku", "quantity-purchased", "promotion-id"]
    rows, total = [], 0.0
    for n in range(240):
        sku = r.choice(PRODUCTS)
        qty = r.choice([1, 1, 2])
        oid = f"{r.randint(100, 999)}-{r.randint(1000000, 9999999)}-{r.randint(1000000, 9999999)}"
        day = dt.date(2024, 3, 1) + dt.timedelta(days=r.randint(0, 28))
        base = {"settlement-id": "", "transaction-type": "Order", "order-id": oid, "merchant-order-id": "", "marketplace-name": "Amazon.in", "fulfillment-id": r.choice(["AFN", "MFN"]),
                "posted-date": day.strftime("%Y-%m-%d"), "posted-date-time": day.strftime("%Y-%m-%dT10:00:00+05:30"), "order-item-code": str(r.randint(10 ** 13, 10 ** 14)), "sku": sku[2], "quantity-purchased": qty}
        principal = sku[1] * qty
        lines = [("ItemPrice", "Principal", principal), ("ItemPrice", "Tax", round(principal * 0.05, 2)), ("ItemFees", "Commission", -round(principal * 0.10, 2)), ("ItemFees", "FBA Pick & Pack Fee", -18.0 * qty)]
        for at, ad, amt in lines:
            rows.append({**base, "amount-type": at, "amount-description": ad, "amount": amt})
            total += amt
        if r.random() < 0.1:
            refund = -principal
            rows.append({**base, "transaction-type": "Refund", "amount-type": "ItemPrice", "amount-description": "Principal", "amount": refund, "quantity-purchased": qty})
            rows.append({**base, "transaction-type": "Refund", "amount-type": "ItemFees", "amount-description": "Commission", "amount": round(principal * 0.10 * 0.5, 2)})
            total += refund + round(principal * 0.10 * 0.5, 2)
    summary = {"settlement-id": "88123456789", "settlement-start-date": "2024-03-01T00:00:00+05:30", "settlement-end-date": "2024-03-29T00:00:00+05:30", "deposit-date": "2024-04-02T00:00:00+05:30",
               "total-amount": round(total, 2), "currency": "INR"}
    df = pd.DataFrame([summary] + rows, columns=head)
    df.to_csv(OUT / "c_amazon_settlement.txt", sep="\t", index=False, encoding="utf-8")


# ------------------------------------------------------------------ (d) Tally-style GST sales register: title rows above the header, a totals row                              [typical pattern]
def tally() -> None:
    r, _ = rng_for(4)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales Register"
    ws.append(["Mehta Traders Private Limited"])
    ws.append(["Sales Register"])
    ws.append(["1-Apr-2024 to 31-Mar-2025"])
    ws.append([])
    head = ["Date", "Particulars", "Voucher Type", "Voucher No.", "GSTIN/UIN", "Quantity", "Rate", "Taxable Value", "CGST", "SGST", "IGST", "Round Off", "Gross Total"]
    ws.append(head)
    ws.merge_cells("A1:F1")
    tv = cg = sg = ig = gt = q = 0.0
    start = dt.date(2024, 4, 1)
    for n in range(1, 221):
        name, _, _ = person(r)
        qty = r.randint(1, 40)
        rate = r.choice([120, 250, 480, 900, 1500])
        taxable = qty * rate
        inter = r.random() < 0.3
        c = 0.0 if inter else round(taxable * 0.09, 2)
        i = round(taxable * 0.18, 2) if inter else 0.0
        gross = round(taxable + 2 * c + i)
        d = start + dt.timedelta(days=r.randint(0, 364))
        ws.append([dt.datetime.combine(d, dt.time()), f"{name} & Co", "Sales", f"S/{n:04d}", gstin(r), qty, rate, taxable, c, c, i, round(gross - (taxable + 2 * c + i), 2), gross])
        tv, cg, sg, ig, gt, q = tv + taxable, cg + c, sg + c, ig + i, gt + gross, q + qty
    ws.append(["Grand Total", None, None, None, None, q, None, tv, round(cg, 2), round(sg, 2), round(ig, 2), None, gt])
    wb.properties.created = wb.properties.modified = dt.datetime(2024, 6, 1)
    wb.save(OUT / "d_tally_gst_sales_register.xlsx")


# ------------------------------------------------------------------ (e) transliterated Hindi / mixed-language headers                                                            [typical pattern]
def hindi() -> None:
    r, _ = rng_for(5)
    rows = []
    for n in range(1, 241):
        name, _, phone = person(r)
        p = r.choice(PRODUCTS)
        q = r.randint(1, 6)
        rows.append({"Bill No": 5000 + n, "Tarikh": (dt.date(2024, 5, 1) + dt.timedelta(days=r.randint(0, 90))).strftime("%d/%m/%Y"), "Grahak ka naam": name, "Mobile no.": phone,
                     "Shehar": r.choice(CITIES)[0], "Saman (Product)": p[0], "Matra": q, "Dar (Rate)": p[1], "Kul rashi": p[1] * q, "Bhugtan sthiti": r.choice(["Paid", "Baaki", "Paid", "Aadha"]),
                     "Vikreta": r.choice(["Ramesh", "Sunita", "Gopal"])})
    pd.DataFrame(rows).to_csv(OUT / "e_hindi_headers.csv", index=False, encoding="utf-8-sig")


# ------------------------------------------------------------------ (f) an 80-column table                                                                                    [typical pattern]
def wide() -> None:
    r, g = rng_for(6)
    n = 300
    d = {"record_id": [f"R{i:05d}" for i in range(n)], "order_date": [(dt.date(2024, 1, 1) + dt.timedelta(days=int(x))).isoformat() for x in g.randint(0, 300, n)],
         "region": [r.choice(["North", "South", "East", "West"]) for _ in range(n)], "channel": [r.choice(["Online", "Store", "Partner"]) for _ in range(n)], "status": [r.choice(["Closed", "Open", "Lost"]) for _ in range(n)]}
    for k in range(1, 26):
        d[f"category_{k:02d}"] = [r.choice(["A", "B", "C", "D"][: 2 + k % 3]) for _ in range(n)]
    for k in range(1, 26):
        d[f"metric_{k:02d}"] = np.round(g.gamma(2 + k % 5, 40 + k, n), 2)
    for k in range(1, 21):
        d[f"flag_{k:02d}"] = g.randint(0, 2, n)
    d["revenue"] = np.round(g.gamma(3, 800, n), 2)
    d["quantity"] = g.randint(1, 20, n)
    d["notes"] = [r.choice(["", "call back", "repeat customer", "gift"]) for _ in range(n)]
    d["discount_pct"] = np.round(g.uniform(0, 25, n), 1)
    d["cost"] = np.round(np.array(d["revenue"]) * g.uniform(0.4, 0.8, n), 2)
    d["rating"] = g.randint(1, 6, n)
    pd.DataFrame(d).to_csv(OUT / "f_wide_80_columns.csv", index=False)


# ------------------------------------------------------------------ (g) mixed date formats (a likely day/month swap) and amounts like "₹1,25,000", "Rs. 12.5k", "1.25 lakh"        [typical pattern]
def mixed() -> None:
    r, _ = rng_for(7)
    rows = []
    for n in range(1, 261):
        d = dt.date(2024, 1, 1) + dt.timedelta(days=r.randint(0, 330))
        style = r.randint(0, 4)
        ds = [d.strftime("%d/%m/%Y"), d.strftime("%Y-%m-%d"), d.strftime("%d %b %Y"), d.strftime("%m/%d/%Y"), d.strftime("%d-%m-%y")][style]
        amount = r.choice([4500, 7500.5, 12500, 98000, 125000, 250000, 15000])
        astyle = r.randint(0, 4)
        if astyle == 0:
            a = "₹" + (f"{int(amount):,}" if amount < 1000 else _indian(int(amount)))
        elif astyle == 1:
            a = f"Rs. {amount / 1000:g}k"
        elif astyle == 2:
            a = f"{amount / 100000:g} lakh"
        elif astyle == 3:
            a = str(amount)
        else:
            a = f"₹ {amount:,.2f}"
        rows.append({"Invoice": f"INV-{n:04d}", "Date": ds, "Customer": person(r)[0], "Amount": a, "Mode": r.choice(["UPI", "Cash", "Card"])})
    pd.DataFrame(rows).to_csv(OUT / "g_mixed_dates_amounts.csv", index=False, encoding="utf-8")


def _indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


# ------------------------------------------------------------------ (h) quantities but no money column                                                                          [typical pattern]
def quantities() -> None:
    r, _ = rng_for(8)
    rows = []
    for n in range(1, 281):
        p = r.choice(PRODUCTS)
        q = r.randint(1, 30)
        rows.append({"order_id": 70000 + n, "order_date": (dt.date(2024, 6, 1) + dt.timedelta(days=r.randint(0, 120))).isoformat(), "product": p[0], "warehouse": r.choice(["Bhiwandi", "Gurgaon", "Hosur"]),
                     "quantity": q, "returned_qty": r.choice([0, 0, 0, 1, 2]) if q > 2 else 0, "status": r.choice(["shipped", "delivered", "delivered", "cancelled"])})
    pd.DataFrame(rows).to_csv(OUT / "h_quantities_no_money.csv", index=False)


# ------------------------------------------------------------------ (i) messy headers, "-" and "N/A" as blanks, scientific-notation order ids, phone numbers without their leading zeros   [typical pattern]
def messy() -> None:
    r, _ = rng_for(9)
    rows = []
    for n in range(1, 221):
        name, email, _ = person(r)
        p = r.choice(PRODUCTS)
        q = r.randint(1, 5)
        phone = r.choice(["0", "0"]) + str(r.randint(10 ** 8, 10 ** 9 - 1))               # a 10-digit number written with a leading zero
        rows.append({"Order No ": float(f"{r.randint(100, 999)}{r.randint(100, 999)}000000"), "Customer ": name, "Phone": int(phone), "Email": email if r.random() > 0.15 else "N/A", "Amount": p[1] * q,
                     "Amount ": round(p[1] * q * 0.05, 2), "": "", " ": None, "Qty": q, "Status": r.choice(["Delivered", "-", "Returned", "N/A", "Delivered"]), "Date": (dt.date(2024, 7, 1) + dt.timedelta(days=r.randint(0, 90))).isoformat(),
                     "Empty": None, "Remarks": r.choice(["-", "N/A", "ok", ""])})
    df = pd.DataFrame(rows)
    df["Order No "] = df["Order No "].map(lambda v: f"{v:.5E}")           # the order ids come out as 2.90285E+11, as when a spreadsheet exports a long number
    df.to_csv(OUT / "i_messy_headers.csv", index=False)


README = """# Varied sales exports (synthetic)

Made by `scripts/make_varied_datasets.py` (deterministic, seeded). No real personal data: every name, e-mail, phone number and GSTIN is made up.
"documented" = the layout follows the platform's public documentation; "typical pattern" = an approximation of what such an export looks like in practice.

| file | what it imitates | layout |
|---|---|---|
| `a_shopify_orders.csv` | a Shopify orders export: one row per line item, order-level fields blank on the extra rows, 57 columns, dates with a time and a UTC offset | **documented** |
| `b_flipkart_payout.xlsx` | a Flipkart payout workbook: Help, Orders, Returns and Cancellations sheets; each fee column has a matching waiver column | **documented** |
| `c_amazon_settlement.txt` | an Amazon settlement flat file: tab-delimited, a summary row first, fees and refunds as separate negative rows | **typical pattern** |
| `d_tally_gst_sales_register.xlsx` | a Tally GST sales register: title rows above the header (one merged), a Grand Total row | **typical pattern** |
| `e_hindi_headers.csv` | headers in transliterated Hindi mixed with English | **typical pattern** |
| `f_wide_80_columns.csv` | an 80-column table | **typical pattern** |
| `g_mixed_dates_amounts.csv` | mixed date formats with a likely day/month swap; amounts like "₹1,25,000", "Rs. 12.5k", "1.25 lakh" | **typical pattern** |
| `h_quantities_no_money.csv` | quantities but no money column | **typical pattern** |
| `i_messy_headers.csv` | duplicate, blank and padded headers, empty columns, "-" and "N/A" as blanks, order ids in scientific notation, phone numbers that lost their leading zeros | **typical pattern** |
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for f in (shopify, flipkart, amazon, tally, hindi, wide, mixed, quantities, messy):
        f()
    (OUT / "README.md").write_text(README, encoding="utf-8")
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
