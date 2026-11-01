"""Reading a file into tables the way a person reads it: the right text encoding and delimiter, the header on its real row (title rows above it are skipped), merged cells filled down, empty columns
and non-data sheets left out, and totals and summary rows taken out of the data. Every such change is written to the table's `attrs["load_notes"]` (step, detail, count) and ends up in the cleaning log.

Nothing here knows a file, a platform, a column name or a value: it works from the shape of the rows (how many cells are filled, how text-like they are, whether a row repeats the column sums)."""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

HEADER_SCAN = 60                  # rows looked at to find the header
TOTAL_LABEL = re.compile(r"^\s*(?:grand\s+|sub\s*|net\s+|page\s+|column\s+)?totals?\b|^\s*(?:grand\s+)?sum\b|^\s*total\s*:|^\s*all\s+(?:items|rows)\s*$", re.I)
UNNAMED = re.compile(r"^Unnamed:\s*\d+(?:_level_\d+)?$")


# ------------------------------------------------------------------ text
def decode(content: bytes) -> tuple[str, str]:
    """(text, encoding name). A byte-order mark decides first; otherwise UTF-8 when it is valid UTF-8, else Windows-1252 (the encoding of most spreadsheet exports that are not UTF-8)."""
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace"), "utf-16"
    if len(content) > 3 and content[1:2] == b"\x00" and content[3:4] == b"\x00":          # UTF-16 without a mark: every other byte is zero
        return content.decode("utf-16-le", errors="replace"), "utf-16-le"
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace"), "cp1252"


def sniff_delimiter(sample: str) -> str:
    """The delimiter that splits the first lines into the same number (more than one) of fields; the sniffer's guess otherwise."""
    lines = [ln for ln in sample.splitlines()[:HEADER_SCAN * 2] if ln.strip()]
    best, score = None, 0
    for d in (",", "\t", ";", "|"):
        counts = [len(next(csv.reader([ln], delimiter=d), [])) for ln in lines]
        if not counts or max(counts) < 2:
            continue
        common = max(set(counts), key=counts.count)
        s = common * counts.count(common)
        if common > 1 and s > score:
            best, score = d, s
    if best:
        return best
    try:
        return csv.Sniffer().sniff(sample[:20000], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


# ------------------------------------------------------------------ the header row
def _filled(row) -> int:
    return sum(1 for v in row if not _blank(v))


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and not v.strip())


def _texty(v) -> bool:
    return isinstance(v, str) and bool(v.strip()) and not re.fullmatch(r"[\s\d.,%₹$€£+\-()/:]+", v)


def header_row(rows: list[list]) -> int:
    """Index of the header row. Title rows above it have only a cell or two filled; the header is the first row that is nearly as wide as the table, is made of words rather than numbers, and has
    rows with a similar number of cells below it. 0 when nothing looks like a header (then the first row is taken, as before)."""
    scan = rows[:HEADER_SCAN]
    widths = [_filled(r) for r in scan]
    if not widths or max(widths) < 2:
        return 0
    wide = sorted(widths)[int(0.8 * (len(widths) - 1))]          # the typical width of the rows that are part of the table
    wide = max(wide, 2)
    for i, r in enumerate(scan):
        f = widths[i]
        if f < max(2, 0.6 * wide):
            continue
        cells = [v for v in r if not _blank(v)]
        if sum(_texty(v) for v in cells) < 0.7 * len(cells):
            continue
        below = [w for w in widths[i + 1:i + 6] if w > 0]
        if below and np.median(below) >= 0.4 * f:
            return i
    return 0


def pad(rows: list[list]) -> list[list]:
    width = max((len(r) for r in rows), default=0)
    return [list(r) + [None] * (width - len(r)) for r in rows]


# ------------------------------------------------------------------ names
def clean_headers(names: list) -> list[str]:
    """Trim, give a blank or "Unnamed: n" header a placeholder, and make every name unique (the second "Amount" becomes "Amount_2")."""
    seen: dict[str, int] = {}
    out = []
    for i, c in enumerate(names):
        name = "" if c is None or (isinstance(c, float) and np.isnan(c)) else " ".join(str(c).split())
        if UNNAMED.match(name):
            name = ""
        name = name or f"column_{i + 1}"
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


# ------------------------------------------------------------------ rows that are not records
def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]


def drop_total_rows(df: pd.DataFrame, notes: list[dict]) -> pd.DataFrame:
    """Rows that are a total of the rows above them: their label says so (Total, Grand Total, Sub Total ...), or several of their figures equal the sums of the figures above."""
    if len(df) < 4:
        return df
    drop, labels = [], []
    num = _numeric_cols(df)
    text = [c for c in df.columns if c not in num]
    tail = set(range(max(0, len(df) - 6), len(df)))
    for pos in range(len(df)):
        row = df.iloc[pos]
        lab = next((str(row[c]) for c in text if isinstance(row[c], str) and row[c].strip()), "")
        by_label = bool(TOTAL_LABEL.search(lab)) and (pos in tail or df.iloc[pos][num].notna().sum() >= 1)
        by_sum = False
        if pos in tail and pos > 2 and len(num) >= 2:
            above = df.iloc[:pos][num].sum(skipna=True)
            hit = sum(1 for c in num if pd.notna(row[c]) and above[c] != 0 and abs(row[c] - above[c]) <= 0.005 * abs(above[c]) + 1e-9)
            by_sum = hit >= 2
        if by_label or by_sum:
            drop.append(pos)
            labels.append(lab or "a row of column sums")
    if not drop:
        return df
    notes.append({"step": "Removed totals rows", "detail": f"removed {len(drop)} totals row{'s' if len(drop) != 1 else ''} ({', '.join(repr(x) for x in dict.fromkeys(labels))}) so no figure is counted twice", "count": len(drop)})
    return df.drop(df.index[drop]).reset_index(drop=True)


def drop_summary_rows(df: pd.DataFrame, notes: list[dict]) -> pd.DataFrame:
    """A row whose filled cells all sit in columns that are empty in every other row (a settlement summary above the transactions, a report header line): it describes the file, not a record.
    The row is removed, its values go in the note, and the columns it alone filled are left out."""
    if len(df) < 5:
        return df
    notna = df.notna()
    counts = notna.sum()
    only_one = [c for c in df.columns if counts[c] == 1]
    if len(only_one) < 2:
        return df
    drop = []
    for pos in range(len(df)):
        filled = [c for c in df.columns if notna.iloc[pos][c]]
        mine = [c for c in only_one if notna.iloc[pos][c]]
        if len(mine) >= 2 and len(mine) == len(filled):
            drop.append((pos, mine))
    if not drop:
        return df
    drop = drop[:3]
    parts = []
    for pos, cols in drop:
        parts.append(", ".join(f"{c} = {df.iloc[pos][c]}" for c in cols)[:200])
    cols_gone = sorted({c for _, cols in drop for c in cols})
    out = df.drop(df.index[[p for p, _ in drop]]).drop(columns=cols_gone).reset_index(drop=True)
    notes.append({"step": "Removed a summary row", "detail": f"removed {len(drop)} row{'s' if len(drop) != 1 else ''} that described the file rather than one record ({'; '.join(parts)}); the {len(cols_gone)} columns only it filled were left out", "count": len(drop)})
    return out


def drop_empty_columns(df: pd.DataFrame, notes: list[dict]) -> pd.DataFrame:
    empty = [c for c in df.columns if df[c].isna().all() or (df[c].map(lambda v: isinstance(v, str) and not v.strip()).all())]
    if not empty:
        return df
    notes.append({"step": "Left out empty columns", "detail": f"left out {len(empty)} column{'s' if len(empty) != 1 else ''} with nothing in {'them' if len(empty) != 1 else 'it'} ({', '.join(repr(c) for c in empty[:6])}{' …' if len(empty) > 6 else ''})", "count": len(empty)})
    return df.drop(columns=empty)


def finish(df: pd.DataFrame, notes: list[dict], skipped_above: int = 0) -> pd.DataFrame:
    df = df.copy()
    df.columns = clean_headers(list(df.columns))
    df = df.dropna(how="all").reset_index(drop=True)
    df = drop_empty_columns(df, notes)
    df = drop_summary_rows(df, notes)
    df = drop_total_rows(df, notes)
    df = df.dropna(how="all").reset_index(drop=True)
    if skipped_above:
        notes.insert(0, {"step": "Skipped title rows", "detail": f"the header is on row {skipped_above + 1}; the {skipped_above} row{'s' if skipped_above != 1 else ''} above it (a title or a period) {'are' if skipped_above != 1 else 'is'} not data", "count": skipped_above})
    df.attrs["load_notes"] = notes
    return df


def keep_leading_zeros(typed: pd.DataFrame, as_text: pd.DataFrame) -> pd.DataFrame:
    """A column of digits where some value starts with 0 (a postal code, a phone number, a code) is kept as text, so the zeros survive."""
    for c in typed.columns:
        if c in as_text.columns and pd.api.types.is_numeric_dtype(typed[c]) and not pd.api.types.is_bool_dtype(typed[c]):
            s = as_text[c].dropna().astype(str)
            if len(s) and s.str.fullmatch(r"0\d+").any() and s.str.fullmatch(r"\d+").all():
                typed[c] = as_text[c]
    return typed


# ------------------------------------------------------------------ files
def read_delimited(content: bytes, name: str = "file") -> pd.DataFrame:
    text, enc = decode(content)
    sep = sniff_delimiter(text[:30000])
    rows = list(csv.reader(io.StringIO(text[:200000 if len(text) > 200000 else len(text)]), delimiter=sep))
    h = header_row([r for r in rows])
    notes: list[dict] = []
    if enc not in ("utf-8", "utf-8-sig"):
        notes.append({"step": "Read the file's text encoding", "detail": f"the file is not UTF-8; it was read as {enc} so accented and non-English characters stay readable", "count": 0})
    try:
        kw = dict(sep=sep, skiprows=h, skip_blank_lines=True, low_memory=False)
        df = pd.read_csv(io.StringIO(text), **kw)
        as_text = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, **kw)
    except (pd.errors.ParserError, ValueError):
        body = pad(list(csv.reader(io.StringIO(text), delimiter=sep))[h:])
        df = pd.DataFrame(body[1:], columns=body[0]).replace("", np.nan)
        as_text = df
        notes.append({"step": "Fixed uneven rows", "detail": "some rows had more or fewer fields than the header; short rows were padded and the fields lined up from the left", "count": 0})
    df = keep_leading_zeros(df, as_text)
    return finish(df, notes, skipped_above=h)


def _fill_merged(ws) -> None:
    """A tall merged cell (a group name spanning several rows) holds its value in the top cell only: copy it down so every row of the group carries it. Wide title merges are left alone."""
    for rng in list(ws.merged_cells.ranges):
        if rng.max_row > rng.min_row:
            top = ws.cell(rng.min_row, rng.min_col).value
            ws.unmerge_cells(str(rng))
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    ws.cell(r, c).value = top
        else:
            ws.unmerge_cells(str(rng))


def _sheet_to_frame(rows: list[list], sheet: str) -> pd.DataFrame | None:
    rows = [r for r in rows]
    while rows and _filled(rows[-1]) == 0:
        rows.pop()
    if not rows:
        return None
    rows = pad(rows)
    h = header_row(rows)
    body = rows[h:]
    if len(body) < 2:
        return None
    df = pd.DataFrame(body[1:], columns=[("" if _blank(v) else v) for v in body[0]]).infer_objects()
    return finish(df, [], skipped_above=h)


def looks_like_instructions(df: pd.DataFrame) -> bool:
    """A sheet of notes rather than records: one or two columns of long sentences, or nothing below the header."""
    if df is None or df.shape[1] > 2 or len(df) == 0:
        return df is None or len(df) == 0
    text = pd.concat([df[c].dropna().astype(str) for c in df.columns]) if df.shape[1] else pd.Series(dtype=str)
    return bool(len(text)) and float(text.str.len().median()) > 30 and not any(pd.api.types.is_numeric_dtype(df[c]) for c in df.columns)


def read_workbook(path: Path) -> dict[str, pd.DataFrame]:
    """{sheet name: table} for the sheets that hold records."""
    import openpyxl
    out: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    if path.suffix.lower() == ".xls":
        raw = pd.read_excel(path, sheet_name=None, header=None)
        sheets = {k: [list(r) for r in v.itertuples(index=False)] for k, v in raw.items()}
    else:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
        sheets = {}
        for ws in wb.worksheets:
            if ws.sheet_state != "visible":
                continue
            _fill_merged(ws)
            sheets[ws.title] = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    for name, rows in sheets.items():
        df = _sheet_to_frame(rows, name)
        if df is None or df.empty:
            continue
        if looks_like_instructions(df):
            skipped.append(name)
            continue
        out[name] = df
    for k, df in list(out.items())[:1]:             # said once, on the first table
        if skipped:
            df.attrs["load_notes"] = list(df.attrs.get("load_notes") or []) + [{"step": "Left out a sheet of notes", "detail": f"left out the sheet{'s' if len(skipped) != 1 else ''} {', '.join(repr(s) for s in skipped)}: {'they read' if len(skipped) != 1 else 'it reads'} as instructions, not records", "count": len(skipped)}]
    return out
