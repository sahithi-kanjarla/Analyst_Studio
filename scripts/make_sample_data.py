"""Build the multi-file demo sample for a small online seller.

Seeded and deterministic: running it twice gives identical files.
Output: sample_data/demo/files/  (customers.csv, products.csv, orders.csv,
order_lines.xlsx, returns.csv).  What is hidden in the data is listed in
sample_data/demo/README.md.   Run:  python scripts/make_sample_data.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20261104
OUT = Path(__file__).resolve().parent.parent / "sample_data" / "demo" / "files"
rng = np.random.default_rng(SEED)

# ---- products -------------------------------------------------------------
# category: (items, price range, weight in sales, return rate)
CATS = {
    "Kitchen":    (14, (12, 80), 0.27, 0.04),
    "Home Decor": (12, (10, 70), 0.20, 0.05),
    "Apparel":    (16, (15, 90), 0.19, 0.27),   # returns clearly above the rest
    "Garden":     (10, (8, 60), 0.14, 0.04),
    "Toys":       (10, (6, 40), 0.12, 0.05),
    "Stationery": (8, (3, 18), 0.08, 0.03),     # smallest group
}
adj = ["Classic", "Modern", "Compact", "Deluxe", "Everyday", "Starter", "Premium", "Folding"]
nouns = {
    "Kitchen": ["Mixing Bowl", "Chopping Board", "Kettle", "Storage Jar", "Pan", "Colander", "Whisk"],
    "Home Decor": ["Candle Holder", "Cushion Cover", "Wall Frame", "Vase", "Table Lamp", "Rug"],
    "Apparel": ["T-Shirt", "Hoodie", "Scarf", "Jacket", "Cap", "Socks Pack", "Joggers", "Dress"],
    "Garden": ["Plant Pot", "Watering Can", "Trowel Set", "Seed Tray", "Bird Feeder"],
    "Toys": ["Puzzle", "Building Set", "Plush Toy", "Board Game", "Craft Kit"],
    "Stationery": ["Notebook", "Pen Set", "Planner", "Sticker Pack"],
}
rows = []
n = 1
for cat, (k, (lo, hi), _, _) in CATS.items():
    for i in range(k):
        price = round(float(rng.uniform(lo, hi)), 2)
        rows.append({
            "sku": f"SKU-{n:04d}",
            "product_name": f"{adj[i % len(adj)]} {nouns[cat][i % len(nouns[cat])]}",
            "category": cat,
            "unit_cost": round(price * float(rng.uniform(0.35, 0.6)), 2),
            "list_price": price,
        })
        n += 1
products = pd.DataFrame(rows)
products.loc[rng.choice(len(products), 3, replace=False), "unit_cost"] = np.nan  # missing

# ---- customers ------------------------------------------------------------
N_CUST = 1400
REGIONS = ["North", "South", "East", "West", "Central", "Islands"]
R_W = np.array([0.30, 0.20, 0.16, 0.16, 0.14, 0.04])  # North top, Islands bottom
customers = pd.DataFrame({
    "customer_id": [f"C{i:05d}" for i in range(1, N_CUST + 1)],
    "signup_date": pd.to_datetime("2023-10-01") + pd.to_timedelta(rng.integers(0, 800, N_CUST), unit="D"),
    "region": rng.choice(REGIONS, N_CUST, p=R_W),
    "acquisition_channel": rng.choice(["Search", "Social", "Email", "Referral", "Marketplace"],
                                      N_CUST, p=[0.3, 0.25, 0.15, 0.1, 0.2]),
})
customers["signup_date"] = customers["signup_date"].dt.strftime("%Y-%m-%d")
customers.loc[rng.choice(N_CUST, 70, replace=False), "acquisition_channel"] = np.nan
customers.loc[rng.choice(N_CUST, 25, replace=False), "region"] = np.nan

# ---- orders (growing trend) ----------------------------------------------
N_ORD = 3300
months = pd.period_range("2024-01", "2025-12", freq="M")
growth = np.linspace(1.0, 2.6, len(months))          # volume rises ~2.6x over 24 months
growth = growth * (1 + 0.12 * np.sin(np.arange(len(months)) * 2 * np.pi / 12))  # mild seasonality
mp = growth / growth.sum()
order_month = rng.choice(len(months), N_ORD, p=mp)
days = np.array([months[m].days_in_month for m in order_month])
dates = pd.to_datetime([f"{months[m].year}-{months[m].month:02d}-01" for m in order_month]) \
    + pd.to_timedelta((rng.random(N_ORD) * days).astype(int), unit="D")
dates = dates.sort_values()
cust_ids = customers["customer_id"].to_numpy()
orders = pd.DataFrame({
    "order_id": [f"ORD-{100000 + i}" for i in range(N_ORD)],
    "customer_ref": rng.choice(cust_ids, N_ORD),     # links to customers.customer_id
    "order_date": dates,
    "sales_channel": rng.choice(["Web Store", "Marketplace", "Social Shop"], N_ORD, p=[0.55, 0.3, 0.15]),
    "payment_method": rng.choice(["Card", "Wallet", "Bank Transfer", "Cash on Delivery"], N_ORD, p=[0.5, 0.25, 0.1, 0.15]),
    "shipping_fee": np.round(rng.choice([0, 3.5, 4.9, 6.5], N_ORD, p=[0.35, 0.3, 0.25, 0.1]), 2),
})
orders["order_date"] = orders["order_date"].dt.strftime("%Y-%m-%d")
# North customers order more: re-point a share of orders to North customers
north = customers.loc[customers["region"] == "North", "customer_id"].to_numpy()
mask = rng.random(N_ORD) < 0.12
orders.loc[mask, "customer_ref"] = rng.choice(north, mask.sum())
# one inconsistent date format: the last 160 orders are DD/MM/YYYY
tail = orders.index[-160:]
orders.loc[tail, "order_date"] = pd.to_datetime(orders.loc[tail, "order_date"]).dt.strftime("%d/%m/%Y")
orders.loc[rng.choice(N_ORD, 30, replace=False), "payment_method"] = np.nan
orders.loc[rng.choice(N_ORD, 12, replace=False), "customer_ref"] = np.nan

# ---- order lines ----------------------------------------------------------
cat_names = list(CATS)
cat_w = np.array([CATS[c][2] for c in cat_names])
by_cat = {c: products.loc[products["category"] == c, "sku"].to_numpy() for c in cat_names}
price_of = products.set_index("sku")["list_price"].to_dict()
lines = []
lid = 1
for oid in orders["order_id"]:
    for _ in range(int(rng.choice([1, 2, 3, 4], p=[0.5, 0.3, 0.14, 0.06]))):
        c = cat_names[rng.choice(len(cat_names), p=cat_w)]
        sku = rng.choice(by_cat[c])
        lines.append((f"L{lid:06d}", oid, sku, int(rng.choice([1, 1, 1, 2, 2, 3])),
                      round(price_of[sku] * float(rng.choice([1, 1, 1, 0.9, 0.85])), 2)))
        lid += 1
order_lines = pd.DataFrame(lines, columns=["line_id", "order_id", "item_code", "quantity", "unit_price"])
order_lines["quantity"] = order_lines["quantity"].astype(float)
order_lines.loc[rng.choice(len(order_lines), 40, replace=False), "quantity"] = np.nan  # missing

# ---- returns (built before duplicates are added) -------------------------
cat_of = products.set_index("sku")["category"].to_dict()
rate = {c: CATS[c][3] for c in CATS}
ret = order_lines[[rng.random() < rate[cat_of[s]] for s in order_lines["item_code"]]]
reasons_apparel = ["Wrong size", "Wrong size", "Did not fit", "Changed mind", "Faulty"]
reasons_other = ["Damaged in transit", "Changed mind", "Faulty", "Not as described"]
od = orders.set_index("order_id")["order_date"]
rdates = []
for oid in ret["order_id"]:
    d = od[oid]
    d = pd.to_datetime(d, format="%d/%m/%Y") if "/" in d else pd.to_datetime(d)
    rdates.append((d + pd.Timedelta(days=int(rng.integers(3, 30)))).strftime("%Y-%m-%d"))
returns = pd.DataFrame({
    "return_id": [f"RT-{i:05d}" for i in range(1, len(ret) + 1)],
    "line_id": ret["line_id"].to_numpy(),            # links to order_lines.line_id
    "return_date": rdates,
    "reason": [str(rng.choice(reasons_apparel if cat_of[s] == "Apparel" else reasons_other))
               for s in ret["item_code"]],
    "refund_amount": np.round(ret["unit_price"].to_numpy() * ret["quantity"].fillna(1).to_numpy(), 2),
})
returns.loc[rng.choice(len(returns), 15, replace=False), "reason"] = np.nan

# ---- a handful of exact duplicate rows -----------------------------------
order_lines = pd.concat([order_lines, order_lines.sample(18, random_state=SEED)], ignore_index=True)
orders = pd.concat([orders, orders.sample(9, random_state=SEED)], ignore_index=True)

# ---- write ----------------------------------------------------------------
OUT.mkdir(parents=True, exist_ok=True)
customers.to_csv(OUT / "customers.csv", index=False)
products.to_csv(OUT / "products.csv", index=False)
orders.to_csv(OUT / "orders.csv", index=False)
order_lines.to_excel(OUT / "order_lines.xlsx", index=False)
returns.to_csv(OUT / "returns.csv", index=False)
for f in sorted(OUT.iterdir()):
    print(f.name, f.stat().st_size // 1024, "KB")
print("orders", len(orders), "lines", len(order_lines), "returns", len(returns))
