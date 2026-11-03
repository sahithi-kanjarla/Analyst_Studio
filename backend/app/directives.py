"""The CLOSED list of things an answer can do to the analysis. Code executes these; nothing an LLM writes is ever executed.

An action is a dict {"type": <name>, ...parameters}. `validate_action` checks it against the real columns and returns a clean copy
containing only the whitelisted parameters (or None and a reason). `cleaner.apply_directives` is the executor.

    set_date_column         {column}                                 the date used for time analysis
    set_measures            {columns[], pin_main}                    the number(s) the analysis is built around (first = main unless pin_main is false)
    define_derived_metric   {name, kind: ratio_of_sums|difference|product, a, b, use_as_main}
    filter_rows             {column, operator, value}                KEEP the rows where the condition holds
    treat_value_as_missing  {column, values[]}                       placeholder values become missing in the analysis copy
    handle_negatives        {column, mode: keep|exclude|net_against_column, against, name, use_as_main}
    drop_column             {column}                                 the column is left out of the analysis
    merge_columns           {columns[], name, how: first_non_null|sum}
    fill_missing            {column, value}                          blank cells of a numeric column get this number
    set_date_order          {column, dayfirst}                       how the day and month of d/m/y dates are read
    swap_day_month          {column}                                 the day and month of every date that can be swapped (day 12 or less) trade places
    merge_labels            {column, mapping{label: label}}          rare labels are renamed to the common label they abbreviate
    set_order_key           {column}                                 the column that identifies one order (checked against the data before any value is blanked)
    note_only               {}                                       recorded as context for the LLM, the data is not touched
"""
from __future__ import annotations

import re

OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "is_null", "not_null")
DERIVED_KINDS = ("ratio_of_sums", "difference", "product")
NEG_MODES = ("keep", "exclude", "net_against_column")
MERGE_HOW = ("first_non_null", "sum")
ACTION_TYPES = ("set_date_column", "set_measures", "define_derived_metric", "filter_rows", "treat_value_as_missing",
                "handle_negatives", "drop_column", "merge_columns", "fill_missing", "set_date_order", "swap_day_month", "merge_labels", "set_order_key", "note_only")
MAX_LIST = 20

# A default answer must leave the data exactly as it is, so that "Skip, use defaults" never changes a number.
def is_harmless(action: dict) -> bool:
    t = action.get("type")
    return t in ("note_only", "set_date_column", "set_measures", "merge_labels") or (t == "handle_negatives" and action.get("mode") == "keep") or (t == "set_date_order" and action.get("dayfirst") is True)


def changes_meaning(action: dict) -> bool:
    """Can choosing this action change a number or what it means? Notes and "analyse these columns" do not: the app analyses every
    reasonable column anyway. Picking the date is different, because a time axis can follow only one of them."""
    t = action.get("type")
    if t == "set_date_column":
        return True
    return t not in ("note_only", "set_measures") and (t == "merge_labels" or not is_harmless(action))


def _scalar(v):
    if isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str) and v.strip() and len(v) <= 80:
        return v.strip()
    return None


def clean_name(name, taken: set[str], fallback: str = "derived") -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "")).strip("_")[:40] or fallback
    out, n = base, 2
    while out in taken:
        out, n = f"{base}_{n}", n + 1
    return out


def validate_action(action, roles: dict[str, str], taken_names: set[str] | None = None) -> tuple[dict | None, str]:
    """`roles` maps every real column to its role. `taken_names` are column names new columns must not reuse."""
    if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
        return None, f"action '{action.get('type') if isinstance(action, dict) else action}' is not in the allowed list"
    t = action["type"]
    cols = set(roles)
    taken = set(taken_names if taken_names is not None else cols) | cols

    def col(key, *want):
        c = action.get(key)
        if not isinstance(c, str) or c not in cols:
            return None
        return c if not want or roles[c] in want else None

    def col_list(key, *want):
        v = action.get(key)
        if not isinstance(v, list) or not v or len(v) > MAX_LIST:
            return None
        out = [c for c in v if isinstance(c, str) and c in cols and (not want or roles[c] in want)]
        return list(dict.fromkeys(out)) if len(out) == len(v) else None

    if t == "note_only":
        return {"type": t}, ""
    if t == "set_date_column":
        c = col("column", "date")
        return ({"type": t, "column": c}, "") if c else (None, "set_date_column needs an existing date column")
    if t == "set_measures":
        cs = col_list("columns", "numeric", "binary")
        # pin_main False: every column is its own measure and the planner still picks the main one itself
        return ({"type": t, "columns": cs, "pin_main": action.get("pin_main") is not False}, "") if cs else (None, "set_measures needs existing numeric columns")
    if t == "drop_column":
        c = col("column")
        return ({"type": t, "column": c}, "") if c else (None, "drop_column needs an existing column")
    if t == "define_derived_metric":
        want = ("numeric", "categorical") if action.get("kind") == "product" else ("numeric",)      # a quantity with few distinct values is read as a category
        a, b = col("a", *want), col("b", *want)
        if not a or not b or a == b or action.get("kind") not in DERIVED_KINDS:
            return None, "define_derived_metric needs two different numeric columns and kind ratio_of_sums, difference or product"
        return {"type": t, "kind": action["kind"], "a": a, "b": b, "name": clean_name(action.get("name") or f"{a}_{action['kind']}_{b}", taken),
                "use_as_main": action.get("use_as_main") is not False}, ""
    if t == "treat_value_as_missing":
        c = col("column")
        vals = action.get("values")
        vals = [_scalar(v) for v in vals] if isinstance(vals, list) else None
        if not c or not vals or len(vals) > MAX_LIST or any(v is None for v in vals):
            return None, "treat_value_as_missing needs an existing column and a short list of values"
        return {"type": t, "column": c, "values": vals}, ""
    if t == "filter_rows":
        c, op = col("column"), action.get("operator")
        if not c or op not in OPERATORS:
            return None, "filter_rows needs an existing column and an allowed operator"
        if op in ("is_null", "not_null"):
            return {"type": t, "column": c, "operator": op, "value": None}, ""
        if op in ("in", "not_in"):
            vals = action.get("value")
            vals = [_scalar(v) for v in vals] if isinstance(vals, list) else None
            if not vals or len(vals) > MAX_LIST or any(v is None for v in vals):
                return None, "filter_rows in/not_in needs a short list of values"
            return {"type": t, "column": c, "operator": op, "value": vals}, ""
        v = _scalar(action.get("value"))
        return ({"type": t, "column": c, "operator": op, "value": v}, "") if v is not None else (None, "filter_rows needs a plain value")
    if t == "handle_negatives":
        c, mode = col("column", "numeric"), action.get("mode")
        if not c or mode not in NEG_MODES:
            return None, "handle_negatives needs an existing numeric column and mode keep, exclude or net_against_column"
        if mode != "net_against_column":
            return {"type": t, "column": c, "mode": mode}, ""
        against = col("against", "numeric")
        if not against or against == c:
            return None, "net_against_column needs a different existing numeric column in `against`"
        return {"type": t, "column": c, "mode": mode, "against": against, "name": clean_name(action.get("name") or f"{c}_net", taken),
                "use_as_main": action.get("use_as_main") is not False}, ""
    if t == "fill_missing":
        c, v = col("column"), action.get("value")
        if not c or isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, "fill_missing needs an existing column and a number"
        return {"type": t, "column": c, "value": v}, ""
    if t == "set_date_order":
        c = col("column", "date")
        if not c or not isinstance(action.get("dayfirst"), bool):
            return None, "set_date_order needs an existing date column and dayfirst true or false"
        return {"type": t, "column": c, "dayfirst": action["dayfirst"]}, ""
    if t == "swap_day_month":
        c = col("column", "date")
        return ({"type": t, "column": c}, "") if c else (None, "swap_day_month needs an existing date column")
    if t == "merge_labels":
        c, m = col("column"), action.get("mapping")
        pairs = {str(k): _scalar(v) for k, v in m.items()} if isinstance(m, dict) else None
        if not c or not pairs or len(pairs) > MAX_LIST or any(v is None for v in pairs.values()):
            return None, "merge_labels needs an existing column and a short mapping of label to label"
        return {"type": t, "column": c, "mapping": pairs}, ""
    if t == "set_order_key":
        c = col("column")
        return ({"type": t, "column": c}, "") if c else (None, "set_order_key needs an existing column")
    if t == "merge_columns":
        cs = col_list("columns")
        how = action.get("how") or "first_non_null"
        if not cs or len(cs) < 2 or how not in MERGE_HOW or (how == "sum" and any(roles[c] != "numeric" for c in cs)):
            return None, "merge_columns needs two or more existing columns (numeric for sum)"
        return {"type": t, "columns": cs, "how": how, "name": clean_name(action.get("name") or "_".join(cs), taken, "merged")}, ""
    return None, "unsupported action"


def action_columns(action: dict) -> list[str]:
    """Every real column an action touches."""
    cols = []
    for key in ("column", "a", "b", "against"):
        if isinstance(action.get(key), str):
            cols.append(action[key])
    cols += [c for c in action.get("columns") or [] if isinstance(c, str)]
    return list(dict.fromkeys(cols))
