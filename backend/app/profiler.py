"""Deterministic data profiling: infer a semantic role for every column."""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

NA_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "missing", "undefined", "#n/a"}
ID_NAME = re.compile(r"(^|[_\s\-])(id|uuid|guid|index)$|^(id|uuid)[_\s\-]|[a-z]Id$", re.I)


def _tidy_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Trim header whitespace and make names unique, so the profile and the cleaned data always agree on column names."""
    seen: dict[str, int] = {}
    names = []
    for i, c in enumerate(df.columns):
        name = str(c).strip() or f"column_{i + 1}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    df = df.copy()
    df.columns = names
    return df


def _sniff_sep(p: Path) -> str:
    """Only real delimiters are candidates: pandas' own sniffing can pick '.' for a one-column file of decimals."""
    import csv
    sample = p.read_bytes()[:20000].decode("utf-8", errors="replace")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def load_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(p)
    elif ext == ".json":
        df = pd.read_json(p)
    else:
        df = pd.read_csv(p, sep=_sniff_sep(p), encoding_errors="replace")
    return _tidy_headers(df)


def load_tables(path: str | Path) -> dict[str, pd.DataFrame]:
    """One file -> one table, except Excel workbooks with several non-empty sheets (one table per sheet)."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        sheets = {k: v for k, v in pd.read_excel(p, sheet_name=None).items() if not v.dropna(how="all").empty}
        if len(sheets) > 1:
            return {f"{p.stem}_{k}": _tidy_headers(v) for k, v in sheets.items()}
        if len(sheets) == 1:
            return {p.stem: _tidy_headers(next(iter(sheets.values())))}
    return {p.stem: load_table(p)}


def normalize_strings(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip whitespace and turn NA-like tokens into real NaN. Returns (df, number of cells turned to NaN)."""
    df = df.copy()
    changed = 0
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_bool_dtype(s):
            continue
        obj = s.astype("object").map(lambda v: v.strip() if isinstance(v, str) else v)
        mask = obj.map(lambda v: isinstance(v, str) and v.lower() in NA_TOKENS)
        changed += int((mask & s.notna()).sum())
        df[c] = obj.mask(mask, np.nan)
    return df, changed


_CURRENCY = re.compile(r"(?i)\b(?:rs|inr|usd|eur|gbp|rupees?)\b\.?|[,$€£₹%\s]")


def try_numeric(s: pd.Series, thresh: float = 0.9):
    if pd.api.types.is_numeric_dtype(s):
        return s
    t = s.astype("object").map(lambda v: _CURRENCY.sub("", v) if isinstance(v, str) else v)
    out = pd.to_numeric(t, errors="coerce")
    nn = s.notna().sum()
    if nn and out.notna().sum() / nn >= thresh:
        return out
    return None


_DMY = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}")
_DMY_FULL = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\s*$")


def infer_date_order(values: pd.Series) -> tuple[bool | None, bool]:
    """For the d/m/y-style strings among the values decide day-first vs month-first from the data itself; other formats (ISO, dd-Mon-yyyy) are ignored,
    so a column mixing formats still gets an answer. Returns (dayfirst, ambiguous). dayfirst is None when no value is d/m/y-style.
    If every such value is ambiguous (both parts <= 12) we assume day-first and flag it so it can be logged."""
    m = values.astype(str).str.extract(_DMY).dropna().astype(int)
    if m.empty:
        return None, False
    if (m[0] > 12).any():
        return True, False
    if (m[1] > 12).any():
        return False, False
    return True, True


def parse_dates(s: pd.Series, dayfirst: bool | None = None) -> pd.Series:
    """Parse every value on its own, so a column holding several formats is read correctly. Numeric d/m/y values follow `dayfirst` (inferred
    from the whole column when None, day-first by default); everything else (ISO, dd-Mon-yyyy, with or without a time) is unambiguous."""
    if dayfirst is None:
        dayfirst = infer_date_order(s.dropna())[0] is not False
    txt = s.astype("object").map(lambda v: v.strip() if isinstance(v, str) else v)
    parts = txt.astype(str).str.extract(_DMY_FULL)
    num = parts[0].notna() & txt.notna()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if num.any():
        a, b, y = (pd.to_numeric(parts.loc[num, i]) for i in (0, 1, 2))
        y = y.where(y >= 100, y + np.where(y < 70, 2000, 1900))
        day, mon = (a, b) if dayfirst else (b, a)
        out.loc[num] = pd.to_datetime(pd.DataFrame({"year": y, "month": mon, "day": day}), errors="coerce")
    rest = ~num & txt.notna()
    iso = rest & txt.astype(str).str.match(r"^\s*\d{4}[-/.]")      # year-first is never day/month ambiguous, and dayfirst would swap its month and day
    for mask, first in ((iso, False), (rest & ~iso, bool(dayfirst))):
        if mask.any():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out.loc[mask] = pd.to_datetime(txt[mask], errors="coerce", format="mixed", dayfirst=first)
    return out


def try_datetime(s: pd.Series, thresh: float = 0.9):
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
        return None
    nn = s.dropna()
    if nn.empty:
        return None
    sample = nn.astype(str).head(200)
    if sample.str.contains(r"\d").mean() < 0.9:
        return None
    if sample.str.contains(r"[-/:.]|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", case=False, regex=True).mean() < 0.9:
        return None
    parsed = parse_dates(s)
    if parsed.notna().sum() / max(s.notna().sum(), 1) >= thresh:
        return parsed
    return None


ORDINAL_NAME = re.compile(r"rating|score|stars|satisf|grade|rank|nps|severity|priority", re.I)  # ordered scales behave like numbers, not labels


def _numeric_role(name: str, nn: pd.Series) -> str:
    n, u = len(nn), nn.nunique()
    if u == 2:
        return "binary"
    integer_valued = bool(np.all(np.mod(nn.values.astype(float), 1) == 0)) if n else False
    if ID_NAME.search(name) and u / max(n, 1) > 0.9:
        return "id"
    if integer_valued and u == n and n > 20 and nn.is_monotonic_increasing:
        return "id"
    if integer_valued and u <= 8 and not ORDINAL_NAME.search(name):
        return "categorical"
    return "numeric"


def infer_column(s: pd.Series, name: str) -> tuple[str, str | None]:
    """Returns (role, conversion) where conversion in {None, 'numeric', 'date'}."""
    nn = s.dropna()
    n, u = len(nn), nn.nunique()
    if n == 0:
        return "empty", None
    if pd.api.types.is_bool_dtype(s):
        return "binary", None
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date", None
    if pd.api.types.is_numeric_dtype(s):
        return _numeric_role(name, nn), None
    num = try_numeric(s)
    if num is not None:
        return _numeric_role(name, num.dropna()), "numeric"
    dt = try_datetime(s)
    if dt is not None:
        return "date", "date"
    if u == 2:
        return "binary", None
    avg_len = nn.astype(str).str.len().mean()
    if u / n > 0.9 and n > 20:
        return ("text" if avg_len > 40 else "id"), None
    if u <= 50 or u / n <= 0.2:
        return "categorical", None
    return "text", None


def _col_stats(s: pd.Series, role: str, conv: str | None) -> dict:
    work = s
    if conv == "numeric":
        work = try_numeric(s)
    elif conv == "date":
        work = try_datetime(s)
    nn = work.dropna()
    if nn.empty:
        return {}
    if role in ("numeric", "binary", "categorical", "id") and pd.api.types.is_numeric_dtype(nn) and role != "id":
        try:
            return {"min": float(nn.min()), "median": float(nn.median()), "mean": float(nn.mean()),
                    "max": float(nn.max()), "std": float(nn.std() or 0), "skew": float(nn.skew() if len(nn) > 2 else 0)}
        except (TypeError, ValueError):
            return {}
    if role == "date":
        return {"min": str(nn.min())[:10], "max": str(nn.max())[:10]}
    if role in ("categorical", "binary"):
        return {"top": {str(k): int(v) for k, v in nn.astype(str).value_counts().head(5).items()}}
    return {}


def profile_df(df: pd.DataFrame) -> dict:
    norm, _ = normalize_strings(df)
    cols = []
    for c in norm.columns:
        s = norm[c]
        role, conv = infer_column(s, str(c))
        cols.append({
            "name": str(c), "dtype": str(df[c].dtype), "role": role, "convert": conv,
            "missing_pct": round(float(df[c].isna().mean() * 100 if not isinstance(df[c].dtype, pd.StringDtype) else s.isna().mean() * 100), 2),
            "unique": int(s.nunique(dropna=True)),
            "stats": _col_stats(s, role, conv),
        })
    return {"rows": int(len(df)), "cols": int(df.shape[1]), "duplicates": int(df.duplicated().sum()), "columns": cols}


def compact_profile(profile: dict, limit: int = 80) -> list[dict]:
    """What we are willing to show an LLM: names, roles, missingness, summary stats. No raw rows."""
    out = []
    for c in profile["columns"][:limit]:
        item = {"name": c["name"], "role": c["role"], "missing_pct": c["missing_pct"], "unique": c["unique"]}
        st = c.get("stats") or {}
        if "top" in st:
            item["top_values"] = list(st["top"].keys())[:5]
        elif st:
            item["summary"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in st.items() if k in ("min", "median", "max", "skew")}
        out.append(item)
    return out
