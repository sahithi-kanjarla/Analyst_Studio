"""Synthetic data shared by the fix2 tests: a bakery sales file (one row per order, constant cost per item) and helpers for a fake understanding record. Nothing here is used by the app."""
import numpy as np
import pandas as pd

from app import understanding as und


def bakery(n=900, end="2025-10-01", seed=3):
    rng = np.random.default_rng(seed)
    items = [(f"B{i:02d}", f"Item number {i}", ["Bread", "Cake", "Pastry"][i % 3], round(2 + i * 0.75, 2), round(1 + i * 0.3, 2)) for i in range(12)]
    pick = rng.integers(0, 12, n)
    dates = pd.Timestamp(end) - pd.to_timedelta(rng.integers(0, 270, n), unit="D")
    qty = rng.integers(1, 9, n)
    price = np.array([items[i][3] for i in pick])
    cost = np.array([items[i][4] for i in pick])
    return pd.DataFrame({"order_id": [f"ORD-{i:04d}" for i in range(1, n + 1)], "order_date": dates.sort_values().strftime("%Y-%m-%d").values,
                         "item_code": [items[i][0] for i in pick], "item": [items[i][1] for i in pick], "category": [items[i][2] for i in pick],
                         "quantity": qty, "unit_price": price, "unit_cost": cost, "amount": np.round(qty * price, 2), "cost_amount": np.round(qty * cost, 2),
                         "payment_mode": rng.choice(["Cash", "Card", "UPI"], n), "customer_type": rng.choice(["Walk-in", "Regular"], n), "outlet": rng.choice(["North", "South", "East", "West"], n)})


def handicrafts(orders=120, seed=4):
    """Long format: several lines per order; the order's date and total repeat on every line, a product's unit price is the same in every order."""
    rng = np.random.default_rng(seed)
    prods = {f"P{i}": 50 + 20 * i for i in range(8)}
    rows = []
    for o in range(orders):
        lines = [(p, int(rng.integers(1, 4))) for p in rng.choice(list(prods), int(rng.integers(1, 4)), replace=False)]
        total = float(sum(prods[p] * q for p, q in lines))
        for p, q in lines:
            rows.append({"order_id": f"H{o:04d}", "order_date": f"2025-0{1 + o % 9}-{1 + o % 27:02d}", "order_total": total, "product": p, "units": q, "unit_price": prods[p],
                         "region": ["N", "S", "E"][o % 3]})
    return pd.DataFrame(rows)


def record(df, order_key=None, roles=None):
    """A valid understanding record as the AI would write it (source llm), naming `order_key` (a column, "none", or None for no answer)."""
    roles = roles or {}
    reply = {"columns": [{"column": c, "meaning": f"what {c} holds", "role": roles.get(c, "other")} for c in df.columns], "goal_columns": {}}
    if order_key is not None:
        reply["order_key"] = order_key
    parts, errors = und.validate(reply, list(df.columns))
    assert not errors, errors
    return und._record("llm", parts)


def boutique(n=700, end="2025-10-09", seed=5):
    """A boutique-style order-line file: gross, discount and a net_received column, and a last month that stops on day 9 (a part-month export)."""
    rng = np.random.default_rng(seed)
    prods = [(f"Product {chr(65 + i)}", ["Ethnic", "Western", "Accessories"][i % 3], 400 + i * 150) for i in range(10)]
    pick = rng.integers(0, 10, n)
    dates = pd.Timestamp(end) - pd.to_timedelta(rng.integers(0, 250, n), unit="D")
    qty = rng.integers(1, 4, n)
    gross = np.array([prods[i][2] for i in pick]) * qty
    disc = np.round(gross * rng.choice([0, 0.05, 0.1, 0.2], n), 2)
    return pd.DataFrame({"order_id": [f"BT-{i:05d}" for i in range(1, n + 1)], "order_date": dates.sort_values().strftime("%Y-%m-%d").values,
                         "product_name": [prods[i][0] for i in pick], "product_category": [prods[i][1] for i in pick], "sales_channel": rng.choice(["Website", "Marketplace", "Store"], n),
                         "quantity": qty, "gross_amount": gross, "discount_amount": disc, "net_received": np.round(gross - disc, 2)})
