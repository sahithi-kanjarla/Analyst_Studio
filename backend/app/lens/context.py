"""What Lens knows about a run: the data understanding record (column meanings, roles, display names, units) turned into a small, checked description of the
cleaned table. This is the ONLY description of the data the AI ever sees; it never sees rows. Nothing here knows a column name or a domain word: roles come from the
understanding record (or the profile when there is no AI record) and are cross-checked against the real dtypes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .. import understanding as undmod

ROLES = ("measure", "category", "date", "id", "flag", "free_text", "other")
CURRENCY = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
PROFILE_ROLE = {"numeric": "measure", "categorical": "category", "binary": "flag", "date": "date", "id": "id", "text": "free_text"}


@dataclass
class Col:
    name: str
    display: str
    role: str
    meaning: str = ""
    kind: str = ""              # money | cost | quantity | count | rate | ratio | "" (what a measure holds)
    ratio: tuple[str, str] | None = None      # (numerator, denominator): a ratio of sums, never summed or averaged row by row


@dataclass
class Ctx:
    cols: dict[str, Col]
    rows: int
    date_col: str | None
    currency: str = ""
    spans: dict[str, tuple[str, str]] = field(default_factory=dict)
    source: str = "profile"       # "understanding" when the AI record described the columns
    memo: dict = field(default_factory=dict)      # things worked out once per run (the opening suggestions), dropped with the data when the run's files change

    def by_role(self, *roles: str) -> list[Col]:
        return [c for c in self.cols.values() if c.role in roles]

    def measures(self) -> list[Col]:
        return self.by_role("measure")

    def groupable(self) -> list[Col]:
        return self.by_role("category", "flag")

    def label(self, name: str) -> str:
        c = self.cols.get(name)
        return c.display if c else str(name)

    def readable(self, text: str) -> str:
        """The text with every internal column name that is not readable as it is (underscores, camelCase) replaced by its display name. Wording only: no figure is touched."""
        names = {n: c.display for n, c in self.cols.items() if c.display and c.display != n and re.search(r"_|[a-z0-9][A-Z]", n)}
        if not names or not text:
            return text
        rx = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?![A-Za-z0-9_])")
        return rx.sub(lambda m: names[m.group(1)], text)

    def find(self, text) -> Col | None:
        """A column by exact name, then case-insensitive name or display name. Never a fuzzy guess."""
        t = str(text or "").strip()
        if t in self.cols:
            return self.cols[t]
        low = t.lower()
        hits = [c for c in self.cols.values() if c.name.lower() == low or c.display.lower() == low]
        return hits[0] if len(hits) == 1 else None

    def cost_columns(self) -> list[Col]:
        return [c for c in self.cols.values() if c.kind == "cost"]

    @property
    def symbol(self) -> str:
        return CURRENCY.get(self.currency.upper(), self.currency + " " if self.currency else "")


def build(result: dict, df: pd.DataFrame) -> Ctx:
    rec = result.get("understanding") or {}
    usable = undmod.usable(rec)
    prof = {c["name"]: c for c in (result.get("profile") or {}).get("columns") or []}
    derived = {d["name"]: d for d in ((result.get("derived_metrics") or {}).get("created") or []) if d.get("kind") == "ratio_of_sums"}
    cols: dict[str, Col] = {}
    for name in df.columns:
        n = str(name)
        r = (rec.get("columns") or {}).get(n) if usable else None
        role = (r or {}).get("role") or PROFILE_ROLE.get((prof.get(n) or {}).get("role"), "other")
        s = df[name]
        if pd.api.types.is_datetime64_any_dtype(s):          # the real type outranks any label
            role = "date"
        elif role == "date":
            role = "other"
        if role == "measure" and not pd.api.types.is_numeric_dtype(s):
            role = "other"
        if role in ("category", "flag") and s.nunique(dropna=True) > 5000:
            role = "other"
        kind = (r or {}).get("measure_kind") or ""
        display = (r or {}).get("display_name") or undmod.readable_name(n)
        ratio = None
        if n in derived:
            d = derived[n]
            display, kind, role = undmod.derived_display(d), "ratio", "measure"
            ratio = (d["a"], d["b"]) if d["a"] in df.columns and d["b"] in df.columns else None
            if ratio is None:
                role = "other"
        cols[n] = Col(n, display, role, ((r or {}).get("meaning") or (derived.get(n) or {}).get("meaning") or "")[:160], kind, ratio)
    spans = {}
    for c in cols.values():
        if c.role == "date":
            s = df[c.name].dropna()
            if len(s):
                spans[c.name] = (str(s.min().date()), str(s.max().date()))
    plan_date = (result.get("plan") or {}).get("date_col")
    date_col = plan_date if plan_date in spans else next(iter(spans), None)
    return Ctx(cols, int(len(df)), date_col, str((result.get("context") or {}).get("currency") or ""), spans, "understanding" if usable else "profile")


def for_prompt(ctx: Ctx) -> list[dict]:
    """The columns as the AI sees them: name, readable label, role, meaning, unit. No values, no rows."""
    out = []
    for c in ctx.cols.values():
        if c.role in ("other", "free_text"):
            continue
        d = {"name": c.name, "label": c.display, "role": c.role}
        if c.meaning:
            d["meaning"] = c.meaning
        if c.role == "measure":
            d["unit"] = {"money": f"money{' (' + ctx.currency + ')' if ctx.currency else ''}", "cost": "money (cost)", "ratio": "ratio of two totals, computed by code", "rate": "rate", "quantity": "quantity", "count": "count"}.get(c.kind, "number")
        out.append(d)
    return out


# ------------------------------------------------------------------ number formatting: the ONE place a figure becomes text
def fmt_num(v: float) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    v = float(v)
    if abs(v) >= 1000 or v == int(v):
        return f"{v:,.0f}"
    return f"{v:,.2f}" if abs(v) >= 1 else f"{v:,.3f}"


def fmt_value(v: float, col: Col | None, ctx: Ctx | None = None) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    if col is not None and col.kind == "ratio":
        return f"{float(v) * 100:.1f}%"
    s = fmt_num(v)
    if col is not None and col.kind in ("money", "cost") and ctx is not None and ctx.symbol:
        s = (f"-{ctx.symbol}{s[1:]}" if s.startswith("-") else f"{ctx.symbol}{s}")
    return s


def pct(v: float) -> str:
    return f"{float(v):.1f}%"
