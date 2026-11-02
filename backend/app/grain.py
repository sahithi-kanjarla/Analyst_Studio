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


MAX_STRUCT_COLS = 80
CONSTANT_SHARE = 0.95          # of the orders with several rows, this share carry one value (or none) of the column: it describes the order
VARYING_SHARE = 0.5            # of the orders with several rows, at least this share have more than one value of the column: it describes the line


def _is_date(s: pd.Series, role: str | None) -> bool:
    return role == "date" or pd.api.types.is_datetime64_any_dtype(s)


def structural_key(df: pd.DataFrame, roles: dict | None = None) -> tuple[str, list[str], list[str]] | None:
    """(key column, order-level columns, line-level columns) found from how the values repeat, not from any name: a column whose values repeat across rows, where a date or an additive amount stays the same
    on every row of the same value (the order's date and total) while at least two other columns change from row to row (the lines). The first such column when several qualify."""
    roles = roles or {}
    n = len(df)
    if n < 20 or df.shape[1] > MAX_STRUCT_COLS:
        return None
    cands = []
    for c in df.columns:
        s = df[c]
        if roles.get(c) in ("numeric", "date", "binary") or pd.api.types.is_datetime64_any_dtype(s) or (pd.api.types.is_float_dtype(s) and not bool((s.dropna() % 1 == 0).all())):
            continue
        k = s.dropna()
        u = k.nunique()
        if len(k) >= 0.9 * n and u >= MIN_KEYS and len(k) / u >= MIN_REPEAT and u <= 0.9 * len(k):
            cands.append(c)
    for c in cands:
        multi = df[df.duplicated(c, keep=False) & df[c].notna()]
        if multi.empty:
            continue
        g = multi.groupby(c)
        const, vary = [], []
        for o in df.columns:
            if o == c:
                continue
            nun = g[o].nunique(dropna=True)
            filled = float((g[o].count() > 0).mean())
            if filled < 0.3:
                continue
            if (nun <= 1).mean() >= CONSTANT_SHARE:
                const.append(o)
            elif (nun > 1).mean() >= VARYING_SHARE:
                vary.append(o)
        anchor = [o for o in const if _is_date(df[o], roles.get(o)) or (pd.api.types.is_numeric_dtype(df[o]) and not pd.api.types.is_bool_dtype(df[o]) and measure_kind(str(o)) == "additive")]
        if anchor and len(vary) >= 2:
            return c, const, vary
    return None


def detect(df: pd.DataFrame, roles: dict | None = None) -> dict | None:
    """{"key", "label", "orders", "rows", "per_order", "order_columns", "line_columns"} when the table has several rows per order, else None. The key is a column named like an order key whose values repeat,
    or, from the way the values repeat (see `structural_key`), a column that behaves like one."""
    keys = key_columns(df)
    c, label, const, vary = None, "Orders", [], []
    if keys:
        c, label = keys[0]
    else:
        found = structural_key(df, roles)
        if found:
            c, const, vary = found
    if c is None:
        return None
    n = int(df[c].nunique(dropna=True))
    return {"key": c, "label": label, "orders": n, "rows": int(df[c].notna().sum()), "per_order": round(float(df[c].notna().sum()) / n, 2), "order_columns": const, "line_columns": vary}


def count_order_amounts_once(df: pd.DataFrame, log: list[dict], roles: dict | None = None) -> pd.DataFrame:
    """Where nearly every order's rows carry the same amount (an order total repeated on each line), keep it on the first row of the order and blank it on the others."""
    g0 = detect(df, roles)
    if not g0:
        return df
    key = g0["key"]
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
