"""Joiner agent: discover how several tables relate and build ONE analysis table without corrupting the data.

Principles (what a careful analyst does):
  * A key must be (nearly) unique on one side and its values must exist on the other side.
  * Never multiply rows. Many-to-one joins enrich; one-to-many tables are AGGREGATED to the parent's grain first.
  * Measure every join (match rate) and refuse the ones that mostly don't match.
  * Report everything: what was linked, what was skipped, and why.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from .llm import ask_json
from .profiler import profile_df
from .measures import has_sum_word
from .tools import to_binary

SAMPLE = 300_000
GENERIC = ("id", "key", "code", "no", "number")


# ------------------------------------------------------------------ key normalisation
def _fmt_key(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().lower()


def norm_key(s: pd.Series) -> pd.Series:
    """101, 101.0 and ' 101 ' all become '101'; 'C001' and 'c001 ' match. Nulls stay null."""
    return s.map(lambda v: np.nan if pd.isna(v) else _fmt_key(v))


def _eligible(s: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
        return False
    nn = s.dropna()
    if nn.empty:
        return False
    if pd.api.types.is_numeric_dtype(s):
        return bool(np.all(np.mod(nn.values.astype(float), 1) == 0))
    return float(nn.astype(str).str.len().mean()) <= 40


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _singular(s: str) -> str:
    s = _norm(s)
    if s.endswith("ies"):
        return s[:-3] + "y"
    return s[:-1] if s.endswith("s") and len(s) > 3 else s


def name_similarity(child_col: str, parent_col: str, parent_table: str) -> float:
    c, p, t = _norm(child_col), _norm(parent_col), _singular(parent_table)
    if c == p and c not in GENERIC:
        return 1.0
    if p in GENERIC and c in tuple(t + g for g in GENERIC):
        return 1.0
    if c == p:
        return 0.0  # two columns that are both just "id" say nothing: every table has one
    if p in GENERIC and c.endswith(p) and len(c) > len(p):
        return 0.5
    short, long_ = sorted((c, p), key=len)
    return 0.6 if len(short) >= 4 and short in long_ else 0.0


# ------------------------------------------------------------------ detection
def detect_relationships(tables: dict[str, pd.DataFrame]) -> list[dict]:
    meta: dict[str, dict[str, dict]] = {}
    for t, df in tables.items():
        meta[t] = {}
        d = df if len(df) <= SAMPLE else df.sample(SAMPLE, random_state=0)
        for c in df.columns:
            if not _eligible(d[c]):
                continue
            k = norm_key(d[c]).dropna()
            if len(k) >= 5:
                meta[t][c] = {"n": len(k), "uniq": int(k.nunique()), "set": set(k.unique()), "counts": k.value_counts()}

    best: dict[frozenset, dict] = {}
    names = list(tables)
    for i, A in enumerate(names):
        for B in names[i + 1:]:
            for ca, ma in meta[A].items():
                for cb, mb in meta[B].items():
                    e = _score_pair(tables, A, ca, ma, B, cb, mb)
                    if e and e["score"] > best.get(frozenset((A, B)), {"score": 0})["score"]:
                        best[frozenset((A, B))] = e

    edges = sorted(best.values(), key=lambda e: -e["score"])
    for e in edges:  # exact metrics for the survivors
        child = norm_key(tables[e["child_table"]][e["child_col"]]).dropna()
        parent_set = set(norm_key(tables[e["parent_table"]][e["parent_col"]]).dropna().unique())
        e["child_row_match"] = round(float(child.isin(parent_set).mean()), 4) if len(child) else 0.0
    return edges


def _score_pair(tables, A, ca, ma, B, cb, mb) -> dict | None:
    a_key, b_key = ma["uniq"] / ma["n"] >= 0.98, mb["uniq"] / mb["n"] >= 0.98
    if not (a_key or b_key):
        return None  # many-to-many: no safe way to join
    extension = False
    if a_key and b_key:
        a_in_b = len(ma["set"] & mb["set"]) / max(len(ma["set"]), 1)
        b_in_a = len(ma["set"] & mb["set"]) / max(len(mb["set"]), 1)
        if a_in_b >= 0.98 and b_in_a < 0.5:       # every A value is a B value but not the reverse: A is an optional extension of B (e.g. one record for some of B's rows)
            parent, extension = B, True
        elif b_in_a >= 0.98 and a_in_b < 0.5:
            parent, extension = A, True
        else:
            parent = A if len(tables[A]) <= len(tables[B]) else B
    else:
        parent = A if a_key else B
    if parent == A:
        pt, pc, pm, ct, cc, cm = A, ca, ma, B, cb, mb
    else:
        pt, pc, pm, ct, cc, cm = B, cb, mb, A, ca, ma
    containment = len(cm["set"] & pm["set"]) / max(len(cm["set"]), 1)           # share of distinct child values found in the parent
    row_match = float(cm["counts"][cm["counts"].index.isin(pm["set"])].sum() / cm["n"])  # share of child ROWS that would find a parent
    ns = name_similarity(cc, pc, pt)
    id_like = _norm(pc).endswith(GENERIC)
    # Judge by rows, not distinct values: a partial parent extract (some orders reference unknown customers) is still a real
    # link. Weak links are reported with their match rate and skipped at build time if under 50%.
    if pm["uniq"] < 5 or row_match < 0.5:
        return None
    if a_key and b_key and ns < 0.6:
        return None  # two unique columns with no name evidence are probably just row numbers
    coverage = len(cm["set"] & pm["set"]) / max(len(pm["set"]), 1)               # share of the parent's keys that the child actually uses
    value_link = containment >= 0.98 and cm["uniq"] >= 20 and pm["uniq"] >= 20 and coverage >= 0.5
    if ns == 0 and not value_link:
        return None  # no name evidence: demand near-perfect, high-cardinality overlap that uses much of the parent (avoids 1..9 vs 1..N and age-vs-id flukes)
    evidence = ns if ns > 0 else 0.7             # a value-only link counts as strong, though not as strong as a matching name
    score = 0.45 * evidence + 0.45 * row_match + 0.1 * (1 if id_like or value_link else 0)
    if score < 0.55:
        return None
    return {"child_table": ct, "child_col": cc, "parent_table": pt, "parent_col": pc, "score": round(score, 3),
            "name_similarity": ns, "containment": round(containment, 4),
            "parent_coverage": round(len(cm["set"] & pm["set"]) / max(len(pm["set"]), 1), 4),
            "cardinality": "one-to-one" if a_key and b_key else "many-to-one", **({"extension": True} if extension else {})}


def describe_edge(e: dict) -> str:
    return (f"{e['child_table']}.{e['child_col']} → {e['parent_table']}.{e['parent_col']} ({e['cardinality']}, "
            f"{e.get('child_row_match', e['containment']):.0%} of {e['child_table']} rows find a match)")


# ------------------------------------------------------------------ choosing the grain
def choose_base(tables: dict[str, pd.DataFrame], edges: list[dict]) -> tuple[str, str]:
    """The finest-grain table: the one furthest down the foreign-key chain. Every parent joins onto it many-to-one, so no row or column is lost.
    The goal never selects a coarser table (that would force the fact table to be aggregated)."""
    parents: dict[str, set[str]] = {}
    for e in edges:
        if not e.get("extension"):      # an optional extension (e.g. returns of some order lines) hangs off its parent; it never makes the base finer
            parents.setdefault(e["child_table"], set()).add(e["parent_table"])

    def depth(t: str, seen: frozenset = frozenset()) -> int:
        return max((1 + depth(p, seen | {t}) for p in parents.get(t, ()) if p not in seen), default=0)

    pick = max(tables, key=lambda t: (depth(t), len(tables[t]), t))
    if depth(pick):
        return pick, f"it is the finest-grain table (it references {len(parents[pick])} other table(s); parents are joined onto it, nothing is aggregated away)"
    return pick, "it is the largest table"


# ------------------------------------------------------------------ building the analysis table
def build_model(tables: dict[str, pd.DataFrame], profiles: dict[str, dict], edges: list[dict], base: str) -> dict:
    joined = tables[base].copy().reset_index(drop=True)
    origin = {c: base for c in joined.columns}
    colmap = {(base, c): c for c in joined.columns}
    in_model = {base}
    steps: list[dict] = []
    warnings: list[dict] = []
    status = {i: {"status": "unused", "reason": "", "method": ""} for i in range(len(edges))}
    for i, e in enumerate(edges):
        if e.get("status") == "rejected":
            status[i] = {"status": "rejected", "reason": e.get("reason", "rejected by reviewer"), "method": ""}

    def active(i):
        return status[i]["status"] == "unused"

    def join_parent(i, e) -> bool:
        nonlocal joined
        pt, pk = e["parent_table"], e["parent_col"]
        fk_name = colmap[(e["child_table"], e["child_col"])]
        right = tables[pt].copy()
        right["__k"] = norm_key(right[pk])
        right = right.dropna(subset=["__k"])
        dup = int(right["__k"].duplicated().sum())
        if dup:
            warnings.append({"severity": "warn", "text": f"{pt}.{pk} has {dup} duplicate key values; kept the first of each so rows are not multiplied."})
            right = right.drop_duplicates("__k")
        left_k = norm_key(joined[fk_name])
        match = float(left_k.isin(right["__k"]).mean())
        if match < 0.5:
            status[i] = {"status": "skipped", "reason": f"only {match:.0%} of rows have a match, so the link is probably wrong", "method": "join"}
            warnings.append({"severity": "warn", "text": f"Skipped {describe_edge(e)}: only {match:.0%} of {base if e['child_table'] == base else e['child_table']} rows match."})
            return False
        newcols = {}
        for c in right.columns:
            if c in ("__k", pk):
                continue
            name = c if c not in joined.columns else f"{pt}_{c}"
            while name in joined.columns or name in newcols.values():
                name += "_x"
            newcols[c] = name
        right = right[["__k"] + list(newcols)].rename(columns=newcols)
        before = len(joined)
        joined["__k"] = left_k.values
        joined = joined.merge(right, how="left", on="__k").drop(columns="__k")
        assert len(joined) == before, "join changed the row count"
        for c, n in newcols.items():
            colmap[(pt, c)] = n
            origin[n] = pt
        colmap[(pt, pk)] = fk_name
        in_model.add(pt)
        status[i] = {"status": "used", "reason": f"{match:.0%} of rows matched", "method": "join"}
        steps.append({"type": "join", "table": pt, "detail": f"Added {len(newcols)} columns from {pt} using {e['child_table']}.{e['child_col']} → {pt}.{pk} ({match:.0%} of rows matched)",
                      "rows_before": before, "rows_after": len(joined), "match_rate": round(match, 4)})
        if match < 0.9:
            warnings.append({"severity": "warn", "text": f"Only {match:.0%} of rows found a match in {pt}; the rest have missing values for its columns."})
        return True

    def aggregate_child(i, e, child_df=None, child_profile=None) -> bool:
        nonlocal joined
        ct, fk, pt, pk = e["child_table"], e["child_col"], e["parent_table"], e["parent_col"]
        child = tables[ct] if child_df is None else child_df
        prof = {c["name"]: c for c in (profiles[ct] if child_profile is None else child_profile)["columns"]}
        key = norm_key(child[fk])
        c2 = child[key.notna()].copy()
        c2["__k"] = key[key.notna()]
        if c2.empty:
            status[i] = {"status": "skipped", "reason": "no usable key values", "method": "aggregate"}
            return False
        g = c2.groupby("__k")
        out = pd.DataFrame({f"{ct}_count": g.size()})
        added_zero = [f"{ct}_count"]
        fk_cols = {x["child_col"] for x in edges if x["child_table"] == ct}  # keys are labels, never averaged or summed
        nums = [c for c, p in prof.items() if p["role"] == "numeric" and c != fk and c not in fk_cols
                and not _norm(c).endswith(GENERIC) and pd.api.types.is_numeric_dtype(c2[c])]
        nums = sorted(nums, key=lambda c: -(1 - prof[c]["missing_pct"] / 100))[:6]
        for c in nums:
            if has_sum_word(c):
                out[f"{ct}_{c}_total"], out[f"{ct}_{c}_avg"] = g[c].sum(), g[c].mean()
                added_zero.append(f"{ct}_{c}_total")
            else:
                out[f"{ct}_{c}_avg"] = g[c].mean()
        for c in [c for c, p in prof.items() if p["role"] == "date" and pd.api.types.is_datetime64_any_dtype(c2[c])][:1]:
            # Recency as a number (days since the latest record). The raw "last date" is deliberately NOT exported:
            # it would look like a time axis to the planner and produce a meaningless trend.
            out[f"{ct}_days_since_{c}"] = (c2[c].max() - g[c].max()).dt.days
        for c in [c for c, p in prof.items() if p["role"] == "categorical" and p["unique"] <= 50 and c != fk][:2]:
            out[f"{ct}_{c}_distinct"] = g[c].nunique()
            added_zero.append(f"{ct}_{c}_distinct")
        for c in [c for c, p in prof.items() if p["role"] == "binary" and c != fk][:2]:
            b, pos = to_binary(c2[c])
            if b is not None:
                c2["__b"] = b
                out[f"{ct}_{c}_{re.sub(r'[^A-Za-z0-9]+', '_', str(pos)).lower()}_rate"] = c2.groupby("__k")["__b"].mean()
        if (pt, pk) not in colmap:  # parent was only summarised, so its rows are not in the analysis table
            status[i] = {"status": "skipped", "reason": f"{pt} is summarised, not row-level, in this model", "method": "aggregate"}
            return False
        fk_name = colmap[(pt, pk)]
        left_k = norm_key(joined[fk_name])
        matched = float(left_k.isin(out.index).mean())
        if matched == 0:
            status[i] = {"status": "skipped", "reason": "no rows in the base table have related records", "method": "aggregate"}
            return False
        right = out.reset_index().rename(columns={"index": "__k"})
        right = right.rename(columns={right.columns[0]: "__k"})
        for c in right.columns:
            if c != "__k" and c in joined.columns:
                right = right.rename(columns={c: f"{c}_x"})
        before = len(joined)
        joined["__k"] = left_k.values
        joined = joined.merge(right, how="left", on="__k").drop(columns="__k")
        assert len(joined) == before, "aggregation changed the row count"
        new = [c for c in right.columns if c != "__k"]
        for c in new:
            origin[c] = f"{ct} (aggregated)"
        for c in added_zero:
            if c in joined.columns:
                joined[c] = joined[c].fillna(0)
        in_model.add(ct)
        status[i] = {"status": "used", "reason": f"aggregated to one row per {pt}.{pk}; {matched:.0%} of rows have related records", "method": "aggregate"}
        steps.append({"type": "aggregate", "table": ct, "detail": f"Summarised {ct} ({len(child):,} rows) to one row per {pt}.{pk} → {len(new)} new columns ({', '.join(new[:5])}{'…' if len(new) > 5 else ''}); {matched:.0%} of {pt} rows have at least one {ct} record",
                      "rows_before": before, "rows_after": len(joined), "match_rate": round(matched, 4)})
        if e.get("child_row_match", 1) < 0.9:
            warnings.append({"severity": "warn", "text": f"{1 - e['child_row_match']:.0%} of {ct} rows reference a {pt}.{pk} that does not exist and are ignored."})
        return True

    for _ in range(4):  # many-to-one links, outward from the base table (snowflake)
        progressed = False
        for i, e in enumerate(edges):
            if active(i) and e["child_table"] in in_model and e["parent_table"] not in in_model:
                progressed |= join_parent(i, e)
        if not progressed:
            break
    def aggregate_chained(i, e) -> bool:
        """Summarise a child that has children of its own: grandchildren -> child first, then child -> base."""
        nonlocal joined
        ct, pt = e["child_table"], e["parent_table"]
        sub_idx = [j for j, x in enumerate(edges) if j != i and active(j) and x["parent_table"] == ct
                   and x["child_table"] not in in_model and x["child_table"] != ct]
        if not sub_idx:
            return aggregate_child(i, e)
        snap = (joined, dict(origin), dict(colmap), set(in_model), len(steps), len(warnings), {j: dict(v) for j, v in status.items()})
        try:
            sub = build_model(tables, profiles, [edges[j] for j in sub_idx], base=ct)
            for k, j in enumerate(sub_idx):
                status[j] = sub["status"][k]
            sub_steps = [{**st, "detail": f"(via {ct}) {st['detail']}"} for st in sub["steps"]]
            sub_warns = [w for w in sub["warnings"] if not w["text"].startswith(("Table '", "Did not use"))]
            enriched = sub["df"]
            if aggregate_child(i, e, enriched, profile_df(enriched)):
                at = next(n for n in range(len(steps) - 1, -1, -1) if steps[n]["table"] == ct)
                steps[at:at] = sub_steps  # chained steps read before the final roll-up
                warnings.extend(sub_warns)
                in_model.update(t for t in tables if any(st["table"] == t for st in sub["steps"]))
                return True
            raise ValueError(status[i]["reason"] or "nothing to aggregate")
        except Exception as ex:  # a join problem must never kill the run
            joined, o, c, im, ns, nw, st = snap
            origin.clear(); origin.update(o); colmap.clear(); colmap.update(c); in_model.clear(); in_model.update(im)
            del steps[ns:], warnings[nw:]
            status.update(st)
            warnings.append({"severity": "warn", "text": f"Could not chain {ct} through to {pt} ({ex}); using {ct} on its own."})
            return aggregate_child(i, e)

    for i, e in enumerate(edges):  # one-to-many links: aggregate the child to the parent's grain
        if active(i) and e["parent_table"] in in_model and e["child_table"] not in in_model:
            aggregate_chained(i, e)
    for i, e in enumerate(edges):
        if active(i):
            status[i] = {"status": "skipped", "reason": "not reachable from the base table without multiplying rows", "method": ""}
            warnings.append({"severity": "info", "text": f"Did not use {describe_edge(e)}: it cannot be added without duplicating rows."})
    unused = [t for t in tables if t not in in_model]
    for t in unused:
        linked = sorted({e["parent_table"] if e["child_table"] == t else e["child_table"] for e in edges if t in (e["child_table"], e["parent_table"])})
        if linked:
            warnings.append({"severity": "info", "text": f"Table '{t}' links to {', '.join(linked)}, but at the '{base}' level it cannot be attached without duplicating rows, so its columns are not used here. "
                                                         f"To analyse it, ask a question at that level (e.g. a goal about {linked[0]} rows) and re-run."})
        else:
            warnings.append({"severity": "warn", "text": f"Table '{t}' was not connected to the analysis (no reliable key link found), so its data is not used."})
    return {"df": joined, "steps": steps, "warnings": warnings, "origin": origin, "status": status, "unused_tables": unused}


# ------------------------------------------------------------------ optional LLM review
def llm_review(llm, profiles: dict[str, dict], edges: list[dict], goal: str) -> dict | None:
    """The LLM may pick the base table and veto suspicious links. Anything invalid is ignored."""
    if llm is None:
        return None
    payload = {
        "tables": {t: [f"{c['name']} ({c['role']})" for c in p["columns"][:25]] + ([f"...{p['cols'] - 25} more"] if p["cols"] > 25 else []) for t, p in profiles.items()},
        "candidate_links": [{"index": i, **{k: e[k] for k in ("child_table", "child_col", "parent_table", "parent_col", "cardinality", "child_row_match", "name_similarity")}} for i, e in enumerate(edges)],
    }
    out = ask_json(llm,
                   "You are a data modeller reviewing automatically detected table relationships for an analysis goal. "
                   "Veto any link that looks coincidental (e.g. unrelated ids that merely overlap). "
                   "Return JSON: {\"reject_links\": [link indexes], \"reason\": \"one sentence\"}",
                   f"GOAL: {goal}\n{json.dumps(payload, default=str)}")
    if not out:
        return None
    res = {"base_table": out.get("base_table") if out.get("base_table") in profiles else None,
           "reject_links": [i for i in (out.get("reject_links") or []) if isinstance(i, int) and 0 <= i < len(edges)],
           "reason": str(out.get("reason") or "")[:300]}
    return res
