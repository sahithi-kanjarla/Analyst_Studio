"""One row per line item, not per order: files where an order, an invoice or a settlement spans several rows.

A column whose name says it identifies an order, invoice, bill, voucher, receipt, transaction or settlement, and whose values repeat across rows, is the file's order key. Then
  * the file's order count is the number of different keys, never the number of rows (`detect`), and
  * an amount that every row of an order repeats (an order total written on each line item) is counted once per order (`count_order_amounts_once`), so a total is not multiplied by the number of lines.
Nothing here knows a platform or a column's exact name: the key is found from words in its name and from how its values repeat."""
from __future__ import annotations

import re

import pandas as pd

from .measures import measure_kind

KEY_WORDS = {"order": "Orders", "invoice": "Invoices", "bill": "Bills", "voucher": "Vouchers", "receipt": "Receipts", "transaction": "Transactions", "settlement": "Settlements", "purchase": "Purchases", "booking": "Bookings"}
KEY_NAME = re.compile(r"^(?:" + "|".join(KEY_WORDS) + r")s?(?:[\s_\-]*(?:id|no|num|number|code|ref|reference|#))?\.?$", re.I)          # "Order ID", "Invoice No.", "voucher_no", or the bare word; not "Net Settlement" or "Order Date"
MIN_KEYS = 5
MIN_REPEAT = 1.15            # rows per key


def key_columns(df: pd.DataFrame) -> list[tuple[str, str]]:
    """[(column, plural label)] for the columns named like an order key whose values repeat across rows, best first."""
    out = []
    for c in df.columns:
        m = KEY_NAME.search(" ".join(str(c).replace("_", " ").replace("-", " ").split()))
        if not m:
            continue
        if pd.api.types.is_numeric_dtype(df[c]) and not bool((df[c].dropna() % 1 == 0).all()):
            continue                                       # an amount, not a label
        s = df[c].dropna()
        if len(s) < 20:
            continue
        u = s.nunique()
        if u >= MIN_KEYS and len(s) / u >= MIN_REPEAT:
            word = re.match(r"[a-z]+", str(c).strip().lower()).group(0).rstrip("s") if re.match(r"[a-z]+", str(c).strip().lower()) else ""
            word = next((w for w in KEY_WORDS if str(c).lower().startswith(w)), word)
            out.append((c, KEY_WORDS[word], len(s) / u))
    return [(c, label) for c, label, _ in sorted(out, key=lambda x: -x[2])]


def detect(df: pd.DataFrame) -> dict | None:
    """{"key", "label", "orders", "rows", "per_order"} when the table has several rows per order, else None."""
    keys = key_columns(df)
    if not keys:
        return None
    c, label = keys[0]
    n = int(df[c].nunique(dropna=True))
    return {"key": c, "label": label, "orders": n, "rows": int(df[c].notna().sum()), "per_order": round(float(df[c].notna().sum()) / n, 2)}


def count_order_amounts_once(df: pd.DataFrame, log: list[dict]) -> pd.DataFrame:
    """Where nearly every order's rows carry the same amount (an order total repeated on each line), keep it on the first row of the order and blank it on the others."""
    keys = key_columns(df)
    if not keys:
        return df
    key = keys[0][0]
    multi = df[df.duplicated(key, keep=False) & df[key].notna()]
    if multi.empty:
        return df
    fixed = []
    for c in df.columns:
        if c == key or not pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c]) or measure_kind(str(c)) != "additive":
            continue
        g = multi.groupby(key)[c]
        filled = g.count()
        distinct = g.nunique()
        repeated = (filled >= 2) & (filled >= 0.9 * g.size())                    # the amount is on (nearly) every row of the order
        if repeated.mean() >= 0.8 and (distinct[repeated] == 1).mean() >= 0.95:
            fixed.append(c)
    if not fixed:
        return df
    df = df.copy()
    later = df.duplicated(key, keep="first") & df[key].notna()
    n = 0
    for c in fixed:
        n += int((later & df[c].notna()).sum())
        df.loc[later, c] = float("nan")
    log.append({"step": "Counted order amounts once", "detail": f"{', '.join(fixed)} repeat the same figure on every row of an order (several rows per {key}), so each is kept on the first row of the order and blanked on the others; totals now count each order once ({n:,} repeated values blanked)", "count": n})
    return df
