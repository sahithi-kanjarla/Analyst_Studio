"""Deterministic data profiling: infer a semantic role for every column."""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

NA_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "missing", "undefined", "#n/a"}
ID_NAME = re.compile(r"(?i:(^|[_\s\-])(id|uuid|guid|index)$|^(id|uuid)[_\s\-]|(^|[_\s\-])(no|num|nr|number)\.?$)|[a-z]Id$")      # the camelCase form is case-sensitive: customerId yes, Paid / Void / Android no
CODE_SHARE = 0.05      # an integer column with at least this many distinct values per row, and no declared kind, is a code
CODE_MIN_UNIQUE = 50
CODE_FILL = 0.5      # share of the min..max range the distinct values occupy; below it the integers are labels, not quantities


CONTACT_NAME = re.compile(r"(?i)\b(?:phone|mobile|mob|contact|tel|telephone|whatsapp|cell|fax|aadhaar|aadhar)\b|(?:phone|mobile)(?:_?no|_?number)?$")
SCI_ID = re.compile(r"^\s*\d(?:\.\d+)?[eE]\+?\d{6,}\s*$")      # 2.90285E+11: a long number a spreadsheet wrote in scientific notation


def sci_id_like(s: pd.Series) -> bool:
    """Most values are written in scientific notation with a large exponent: a long order or account number that a spreadsheet shortened, not a quantity."""
    t = s.dropna()
    return len(t) > 0 and t.map(lambda v: isinstance(v, str) and bool(SCI_ID.match(v))).mean() >= 0.8


def digit_code_like(name: str, nn: pd.Series) -> bool:
    """Whole numbers of eight or more digits, nearly all with the same number of digits, nearly all different (phone numbers, account numbers), or a column named like a contact detail: labels, never amounts."""
    if not len(nn) or not bool((nn % 1 == 0).all()):
        return False
    if CONTACT_NAME.search(name):
        return True
    digits = nn.abs().astype("int64").astype(str).str.len() if nn.abs().max() < 1e15 else None
    return digits is not None and nn.min() > 0 and (digits >= 8).mean() >= 0.95 and digits.nunique() <= 2 and nn.nunique() >= 0.3 * len(nn) and len(nn) >= 20


def id_like(name: str, s: pd.Series, rule_role: str | None = None, brief_role: str | None = None, kind: str = "other") -> bool:
    """A column that labels rows rather than measures anything: the brief or the rules called it an id, its name says so, or (when no kind was declared for it)
    its values are sparse whole-number codes."""
    return brief_role == "id" or rule_role == "id" or bool(ID_NAME.search(name)) or (kind == "other" and integer_code_like(s))


def integer_code_like(s: pd.Series) -> bool:
    """Whole numbers with many distinct values that sit sparsely in their range (postal codes, phone numbers): labels, so a total or a product of them means nothing."""
    nn = pd.to_numeric(s, errors="coerce").dropna()
    if not len(nn) or nn.nunique() < max(CODE_MIN_UNIQUE, CODE_SHARE * len(s)):
        return False
    return bool((nn % 1 == 0).all()) and nn.nunique() < CODE_FILL * (nn.max() - nn.min() + 1)      # tenure or age fills its range


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
    return _sniff_text(p.read_bytes()[:20000].decode("utf-8", errors="replace"))


def _sniff_text(sample: str) -> str:
    import csv
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def too_many_rows_message(name: str, rows: int, limit: int) -> str:
    return (f"'{name}' has {rows:,} rows. This app analyses up to {limit:,} rows per table; "
            "please split the file or keep the most recent rows, then upload again.")


def count_delimited_rows(content: bytes) -> int:
    """Records in a delimited text file (the header excluded), counting a quoted line break as part of its record."""
    import csv
    import io
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=_sniff_text(text[:20000]))
    count = -1  # the header is not a row
    for _ in reader:
        count += 1
    return max(count, 0)


def check_row_limit(df: pd.DataFrame, name: str, limit: int | None = None) -> None:
    """Backstop after loading: a table over MAX_ROWS stops the run with the same clear message the upload gives."""
    from .config import MAX_ROWS
    limit = MAX_ROWS if limit is None else limit
    if len(df) > limit:
        raise ValueError(too_many_rows_message(name, len(df), limit))


def load_table(path: str | Path) -> pd.DataFrame:
    from . import tableio
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        tables = tableio.read_workbook(p)
        if tables:
            return next(iter(tables.values()))
        df = pd.read_excel(p)
    elif ext == ".json":
        df = pd.read_json(p)
    else:
        return tableio.read_delimited(p.read_bytes(), p.name)
    return _tidy_headers(df)


def load_tables(path: str | Path) -> dict[str, pd.DataFrame]:
    """One file -> one table, except Excel workbooks with several sheets of records (one table per sheet; sheets of notes are left out)."""
    from . import tableio
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        sheets = tableio.read_workbook(p)
        if len(sheets) > 1:
            for k, v in sheets.items():
                check_row_limit(v, f"{p.name} ({k})")
            return {f"{p.stem}_{k}": v for k, v in sheets.items()}
        if len(sheets) == 1:
            check_row_limit(next(iter(sheets.values())), p.name)
            return {p.stem: next(iter(sheets.values()))}
    df = load_table(p)
    check_row_limit(df, p.name)
    return {p.stem: df}


def normalize_strings(df: pd.DataFrame, log: list[dict] | None = None) -> tuple[pd.DataFrame, int]:
    """Strip whitespace and turn blanks into real NaN. NA-like tokens ('none', '-', '?') become NaN only in a column that is otherwise numeric or date
    (where that lets it parse); in a text column they are real labels and stay, logged to `log`. Returns (df, number of cells turned to NaN)."""
    df = df.copy()
    changed = 0
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_bool_dtype(s):
            continue
        obj = s.astype("object").map(lambda v: v.strip() if isinstance(v, str) else v)
        blank = obj.map(lambda v: isinstance(v, str) and v == "")
        tok = obj.map(lambda v: isinstance(v, str) and v != "" and v.lower() in NA_TOKENS)
        if tok.any():
            masked = obj.mask(blank | tok, np.nan)
            if try_numeric(masked) is not None or try_datetime(masked) is not None:
                blank = blank | tok
            elif log is not None:
                for t, n in obj[tok].value_counts().items():
                    log.append({"step": "Kept placeholder as label", "detail": f"kept '{t}' as a label in {c} ({int(n):,} rows)", "count": int(n)})
        changed += int((blank & s.notna()).sum())
        df[c] = obj.mask(blank, np.nan)
    return df, changed


_CURRENCY = re.compile(r"(?i)\b(?:rs|inr|usd|eur|gbp|rupees?)\b\.?|[,$€£₹%\s]")
_SUFFIX = {"k": 1e3, "thousand": 1e3, "l": 1e5, "lac": 1e5, "lacs": 1e5, "lakh": 1e5, "lakhs": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7, "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_AMOUNT = re.compile(r"^(-?)\(?(-?\d+(?:\.\d+)?|\.\d+)\)?([a-z]+)?(-?)$", re.I)


def parse_amounts(t: pd.Series) -> pd.Series:
    """Amounts written for people: "₹1,25,000", "Rs. 12.5k", "1.25 lakh", "(500)" (negative), "2 cr", "3.4 million". Anything else gives NaN."""
    plain = pd.to_numeric(t, errors="coerce")
    left = plain.isna() & t.notna()
    if not left.any():
        return plain
    m = t[left].astype(str).str.lower().str.extract(_AMOUNT)
    mult = pd.to_numeric(m[2].map(lambda w: _SUFFIX.get(w, np.nan) if isinstance(w, str) else 1.0), errors="coerce").astype(float)
    val = (pd.to_numeric(m[1], errors="coerce").astype(float) * mult).to_numpy(dtype=float)
    neg = ((m[0] == "-") | (m[3] == "-") | t[left].astype(str).str.strip().str.startswith("(")).to_numpy(dtype=bool)
    plain = plain.astype(float)
    plain.loc[left] = np.where(neg, -val, val)
    return plain


def try_numeric(s: pd.Series, thresh: float = 0.9):
    if pd.api.types.is_numeric_dtype(s):
        return s
    t = s.astype("object").map(lambda v: _CURRENCY.sub("", v) if isinstance(v, str) else v)
    out = parse_amounts(t)
    nn = s.notna().sum()
    if nn and out.notna().sum() / nn >= thresh:
        return out
    return None


_TZ_SUFFIX = re.compile(r"\s*(?:Z|UTC|GMT|[+-]\d{2}:?\d{2})\s*$", re.I)
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


def date_order_conflict(values: pd.Series) -> tuple[int, int, int]:
    """(values that can only be day-first, values that can only be month-first, values that fit both) among the d/m/y-style strings. Both of the first two present: the column mixes the two orders."""
    m = values.astype(str).str.extract(_DMY).dropna().astype(int)
    if m.empty:
        return 0, 0, 0
    day_first = int((m[0] > 12).sum())
    month_first = int((m[1] > 12).sum())
    return day_first, month_first, int(len(m) - day_first - month_first)


def parse_dates(s: pd.Series, dayfirst: bool | None = None) -> pd.Series:
    """Parse every value on its own, so a column holding several formats is read correctly. Numeric d/m/y values follow `dayfirst` (inferred
    from the whole column when None, day-first by default); everything else (ISO, dd-Mon-yyyy, with or without a time) is unambiguous."""
    if dayfirst is None:
        dayfirst = infer_date_order(s.dropna())[0] is not False
    # a UTC offset written after the time ("2024-03-05 14:22:10 +0530") says where the clock was, not another moment: the date and time are kept as written, so one column never mixes zones
    txt = s.astype("object").map(lambda v: _TZ_SUFFIX.sub("", v.strip()) if isinstance(v, str) and re.search(r"\d:\d\d", v) else v.strip() if isinstance(v, str) else v)
    parts = txt.astype(str).str.extract(_DMY_FULL)
    num = parts[0].notna() & txt.notna()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if num.any():
        a, b, y = (pd.to_numeric(parts.loc[num, i]) for i in (0, 1, 2))
        y = y.where(y >= 100, y + np.where(y < 70, 2000, 1900))
        day, mon = (a, b) if dayfirst else (b, a)
        out.loc[num] = pd.to_datetime(pd.DataFrame({"year": y, "month": mon, "day": day}), errors="coerce")
        bad = num & out.isna()                      # not a date in the chosen order (a 25 where the month goes): a value written the other way round, read that way
        if bad.any():
            a2, b2, y2 = (pd.to_numeric(parts.loc[bad, i]) for i in (0, 1, 2))
            y2 = y2.where(y2 >= 100, y2 + np.where(y2 < 70, 2000, 1900))
            d2, m2 = (b2, a2) if dayfirst else (a2, b2)
            out.loc[bad] = pd.to_datetime(pd.DataFrame({"year": y2, "month": m2, "day": d2}), errors="coerce")
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
    if n and digit_code_like(name, nn):
        return "id"
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
        return ("id" if sci_id_like(s) else _numeric_role(name, num.dropna())), "numeric"
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
            st = {"min": float(nn.min()), "median": float(nn.median()), "mean": float(nn.mean()),
                  "max": float(nn.max()), "std": float(nn.std()), "skew": float(nn.skew()) if len(nn) > 2 else float("nan")}
        except (TypeError, ValueError):
            return {}
        # A statistic that is not a real number is left out, never written as 0: one value has no spread, a constant column has no skew.
        return {k: v for k, v in st.items() if np.isfinite(v)}
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
    out = {"rows": int(len(df)), "cols": int(df.shape[1]), "duplicates": int(df.duplicated().sum()), "columns": cols}
    from .grain import detect
    g = detect(df, {c['name']: c['role'] for c in cols})
    if g:
        out["grain"] = g          # several rows per order: the order count is the number of different keys, not the number of rows
    return out


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
