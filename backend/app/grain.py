"""One row per line item, not per order: files where an order, an invoice or a settlement spans several rows.

The order key is NEVER inferred from how values repeat. Evidence, in order: the user's answer, the AI's understanding record (the column it names as the order or transaction identifier), and, when there is no AI answer, the column's name (order, invoice, bill, transaction...). The data only verifies the candidate (`verify`) and reports what it found (`decide`). With no AI answer, or when the AI and the data disagree, every value is kept and one question is asked. Then
  * the file's order count is the number of different keys, never the number of rows (`detect`), and
  * an amount that every row of an order repeats (an order total written on each line item) is counted once per order (`count_order_amounts_once`), so a total is not multiplied by the number of lines.
Nothing here knows a platform or a column's exact name."""
from __future__ import annotations

import re

import pandas as pd

from .measures import measure_kind

KEY_WORDS = {"order": "Orders", "invoice": "Invoices", "bill": "Bills", "voucher": "Vouchers", "receipt": "Receipts", "transaction": "Transactions", "settlement": "Settlements", "purchase": "Purchases", "booking": "Bookings"}
KEY_NAME = re.compile(r"^(?:" + "|".join(KEY_WORDS) + r")s?(?:[\s_\-]*(?:id|no|num|number|code|ref|reference|#))?\.?$", re.I)          # "Order ID", "Invoice No.", "voucher_no", or the bare word; not "Net Settlement" or "Order Date"
MIN_KEYS = 5
MIN_REPEAT = 1.15            # rows per key


def name_candidates(df: pd.DataFrame) -> list[str]:
    """Columns whose NAME says they identify an order, invoice, bill... (evidence from the name only; repeated or not). Used only when the AI gave no answer."""
    out = []
    for c in df.columns:
        if not KEY_NAME.search(" ".join(str(c).replace("_", " ").replace("-", " ").split())):
            continue
        if pd.api.types.is_numeric_dtype(df[c]) and not bool((df[c].dropna() % 1 == 0).all()):
            continue                                       # an amount, not a label
        if df[c].notna().sum() >= 2:
            out.append(c)
    return out


def label_for(col: str) -> str:
    low = str(col).strip().lower()
    return KEY_WORDS[next((w for w in KEY_WORDS if low.startswith(w)), "order")]


MAX_STRUCT_COLS = 80
CONSTANT_SHARE = 0.95          # of the orders with several rows, this share carry one value (or none) of the column: it describes the order
VARYING_SHARE = 0.5            # of the orders with several rows, at least this share have more than one value of the column: it describes the line
MAX_ATTR_SHARE = 0.5           # a column with more distinct values than this share of the rows is not a product or a category
MAX_QUESTION_OPTIONS = 4


def _is_date(s: pd.Series, role: str | None) -> bool:
    return role == "date" or pd.api.types.is_datetime64_any_dtype(s)


def _is_amount(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def split_columns(df: pd.DataFrame, key: str) -> tuple[list[str], list[str]] | None:
    """(columns that describe the order, columns that describe the line) for rows grouped by `key`: of the keys with several rows, the order-level ones hold one value (or none) on nearly all of them, the line-level ones
    hold several on at least half."""
    multi = df[df.duplicated(key, keep=False) & df[key].notna()]
    if multi.empty:
        return None
    g = multi.groupby(key)
    const, vary = [], []
    for o in df.columns:
        if o == key:
            continue
        nun = g[o].nunique(dropna=True)
        if float((g[o].count() > 0).mean()) < 0.3:
            continue
        if (nun <= 1).mean() >= CONSTANT_SHARE:
            const.append(o)
        elif (nun > 1).mean() >= VARYING_SHARE:
            vary.append(o)
    return const, vary


def is_item_attribute(df: pd.DataFrame, c: str, key: str) -> bool:
    """True when column `c` is constant within a column that groups the rows into products or categories (a product's price or cost): it describes the item, so it is never order-level data and never blanked.
    The grouping column repeats (it is no identifier of one order) and is not just the key again."""
    s = df[c]
    if s.nunique(dropna=True) < 2:
        return False
    n_keys = df[key].nunique(dropna=True)
    for d in df.columns:
        if d in (c, key):
            continue
        t = df[d]
        if pd.api.types.is_datetime64_any_dtype(t) or (pd.api.types.is_float_dtype(t) and not bool((t.dropna() % 1 == 0).all())):
            continue
        u = t.nunique(dropna=True)
        if u < 2 or u > MAX_ATTR_SHARE * len(df) or u >= 0.5 * n_keys:
            continue
        both = df[[d, c]].dropna()
        if len(both) >= 0.8 * len(df) and (both.groupby(d)[c].nunique() <= 1).mean() >= CONSTANT_SHARE and both.groupby(d).size().mean() >= 2:
            return True
    return False


def verify(df: pd.DataFrame, col: str, roles: dict | None = None) -> dict:
    """What the data says about a candidate order key. {"ok": False, "why"} when it cannot be one; otherwise {"ok": True, "unique": bool, "orders", "rows", "per_order", "order_columns", "line_columns", "amount_columns"}.
    Unique per row: the file is already at order grain. Repeated: it must be a real order key, meaning a date or an amount that is NOT a product's attribute stays the same within each value while other columns change."""
    roles = roles or {}
    if col not in df.columns:
        return {"ok": False, "why": f"'{col}' is not in the data"}
    s = df[col]
    nn = s.dropna()
    if pd.api.types.is_numeric_dtype(s) and not bool((nn % 1 == 0).all()):
        return {"ok": False, "why": f"'{col}' holds amounts with decimals, not labels"}
    if len(nn) < 2 or len(nn) < 0.9 * len(df):
        return {"ok": False, "why": f"'{col}' is blank in too many rows"}
    u = int(nn.nunique())
    base = {"ok": True, "orders": u, "rows": int(len(nn)), "per_order": round(len(nn) / u, 2), "order_columns": [], "line_columns": [], "amount_columns": []}
    if u == len(nn):
        return {**base, "unique": True}
    if u < MIN_KEYS:
        return {"ok": False, "why": f"'{col}' has only {u} different values, too few to be one per order"}
    split = split_columns(df, col)
    if split is None:
        return {"ok": False, "why": f"'{col}' does not repeat"}
    const, vary = split
    attrs = {o for o in const if _is_amount(df[o]) and is_item_attribute(df, o, col)}
    const = [o for o in const if o not in attrs]
    vary = vary + [o for o in attrs if o not in vary]
    dates = [o for o in df.columns if o != col and _is_date(df[o], roles.get(o))]
    if dates and not any(o in const for o in dates):          # an order happens on one day: when every date changes within a value of the column, its rows are not one order
        return {"ok": False, "why": f"the date changes within one value of '{col}', so its rows are not one order"}
    anchor = [o for o in const if _is_date(df[o], roles.get(o)) or (_is_amount(df[o]) and measure_kind(str(o)) == "additive")]
    if not anchor or len(vary) < 1:
        return {"ok": False, "why": f"within one value of '{col}' no date or amount stays the same, so it does not behave like an order"}
    amounts = [o for o in const if _is_amount(df[o]) and measure_kind(str(o)) == "additive"]
    return {**base, "unique": False, "order_columns": const, "line_columns": vary, "amount_columns": amounts}


def ai_order_key(rec: dict | None) -> tuple[str | None, str]:
    """(column, state) from the understanding record: state "named" (the AI named the column that identifies one order), "none" (it said no column does) or "unsure" (no usable record, or no answer)."""
    if not rec or rec.get("source") != "llm":
        return None, "unsure"
    k = rec.get("order_key")
    if isinstance(k, str) and k in (rec.get("columns") or {}):
        return k, "named"
    return (None, "none") if k == "none" else (None, "unsure")


def question_candidates(df: pd.DataFrame, roles: dict | None, first: list[str]) -> list[str]:
    """Columns to offer when asking which one identifies an order: those named like a key, the AI's pick, then id-role columns (the user chooses; nothing is decided from values)."""
    roles = roles or {}
    out = list(dict.fromkeys([c for c in first if c in df.columns] + name_candidates(df) + [c for c in df.columns if roles.get(c) == "id"]))
    return [c for c in out if verify(df, c, roles).get("ok") or c in first or c in name_candidates(df)][:MAX_QUESTION_OPTIONS]


def decide(df: pd.DataFrame, roles: dict | None = None, rec: dict | None = None, answered: str | None = None) -> dict:
    """The order key and what was found about it.
    Order of evidence: (1) the user's answer; (2) the understanding record's pick; (3) with no AI answer, columns named like an order key. The data only VERIFIES the candidate.
    verdict: "verified" (several rows per order), "order_grain" (one row per order: nothing to deduplicate), "ask" (no AI answer, or the AI and the data disagree: keep every value and ask which column identifies one order), "none"."""
    roles = roles or {}
    out = {"verdict": "none", "key": None, "source": None, "found": "", "candidates": []}
    if answered == "none":
        return {**out, "source": "answer", "found": "You said no column identifies one order."}
    key, state = (answered, "named") if answered else ai_order_key(rec)
    src = "answer" if answered else "ai"
    if state == "none":
        return {**out, "source": "ai", "found": "The AI found no column that identifies one order."}
    if key:
        v = verify(df, key, roles)
        if v.get("ok"):
            found = (f"'{key}' has {v['orders']:,} different values in {v['rows']:,} rows: one row per order, so nothing is repeated." if v["unique"] else
                     f"'{key}' has {v['orders']:,} different values in {v['rows']:,} rows ({v['per_order']:g} rows per order); {len(v['amount_columns'])} order-level amount(s) repeat on every row.")
            return {**out, **v, "verdict": "order_grain" if v["unique"] else "verified", "key": key, "source": src, "label": label_for(key), "found": found}
        return {**out, "verdict": "ask", "key": key, "source": src, "found": f"{'You' if answered else 'The AI'} named '{key}' as the order key, but the data disagrees: {v['why']}.",
                "candidates": question_candidates(df, roles, [key])}
    cands = question_candidates(df, roles, [])
    if not cands:
        return out
    for c in cands:
        v = verify(df, c, roles)
        if v.get("ok") and v.get("unique"):
            found = f"'{c}' has {v['orders']:,} different values in {v['rows']:,} rows: one row per order, so nothing is repeated."
            return {**out, **v, "verdict": "order_grain", "key": c, "source": "name", "label": label_for(c), "found": found}
    return {**out, "verdict": "ask", "source": "name" if name_candidates(df) else "data", "found": "The AI gave no answer about which column identifies an order." if rec and rec.get("source") == "llm" else "No AI answer about which column identifies an order.", "candidates": cands}


def grain_of(decision: dict | None) -> dict | None:
    """The report-facing part of a decision (only when the key is verified), else None."""
    d = decision or {}
    if d.get("verdict") not in ("verified", "order_grain"):
        return None
    return {k: d[k] for k in ("key", "label", "orders", "rows", "per_order", "order_columns", "line_columns", "amount_columns", "source", "verdict", "found")}


def detect(df: pd.DataFrame, roles: dict | None = None, key: str | None = None) -> dict | None:
    """The grain of a table whose order key is already known (`key`): {"key", "label", ...} when the data verifies it, else None. Never looks for a key by itself."""
    if not key:
        return None
    d = decide(df, roles, None, key)
    return grain_of(d)


def count_order_amounts_once(df: pd.DataFrame, log: list[dict], roles: dict | None = None, key: str | None = None) -> pd.DataFrame:
    """Only for a verified order key with several rows per order: an order-level amount repeated on (nearly) every row of its order stays on the first row and is blanked on the others.
    A value that is constant within a product or category (a price, a cost) is an attribute of the item and is never blanked. A key that is unique per row blanks nothing."""
    g0 = detect(df, roles, key)
    if not g0 or g0["verdict"] != "verified":
        return df
    key = g0["key"]
    multi = df[df.duplicated(key, keep=False) & df[key].notna()]
    fixed = []
    for c in g0["amount_columns"]:
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


def fill_order_details(df: pd.DataFrame, log: list[dict], roles: dict | None = None, key: str | None = None) -> pd.DataFrame:
    """Where an order's own details (its status, date, city) are written on its first row only, every row of the order gets them, so a line's value can be split by what its order is. Words and dates only:
    an order's amounts are not copied (they are counted once, see `count_order_amounts_once`)."""
    g = detect(df, roles, key)
    if not g or g["verdict"] != "verified":
        return df
    key = g["key"]
    cols = [c for c in g["order_columns"] if c in df.columns and not (pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])) and df[c].isna().any()]
    if not cols:
        return df
    df = df.copy()
    n = 0
    for c in cols:
        first = df.groupby(key)[c].transform("first")
        fill = df[c].isna() & first.notna() & df[key].notna()
        n += int(fill.sum())
        df.loc[fill, c] = first[fill]
    if n:
        log.append({"step": "Filled order details down", "detail": f"{len(cols)} columns that describe the order ({', '.join(cols[:5])}{' ...' if len(cols) > 5 else ''}) were written on its first row only; every row of the order now carries them ({n:,} blanks filled), so what a line is worth can be split by its order's status, date or place", "count": n})
    return df
