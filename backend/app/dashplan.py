"""The dashboard plan: which charts and filters the Dashboard shows, and how big each tile is.

Everything here is computed by code from the saved selection (`curation` in result.json) and from the dashboard's own data
spec.  No column name, example value or audience is written in this file.

* A **tile** is one chart: the time view, one interactive chart per category column, or the chart of a kept finding
  (a merged finding is one tile that lists each of its measures).
* The tiles that survive the same cut the other tabs use (`curate.apply_cut`) are shown; the rest are the "More" list.
* The **layout** is a slicing of a fixed grid, so the tiles always cover the whole area: no gap, no overlap, no filler.
  Weights come from importance, so the top tile is larger, and with few tiles each one is bigger.
"""
from __future__ import annotations

from . import curate

GRID_COLS = 24                 # the grid the tiles are cut from (units, not pixels)
GRID_ROWS = 24
MIN_TILE_AREA = 48             # the smallest readable tile, in grid units (8 x 6)
MAX_TILES = GRID_COLS * GRID_ROWS // MIN_TILE_AREA      # what one screen holds; a bigger kept set spills into "More"
MIN_COLS = 6                   # a tile is never narrower or shorter than this unless the space forces it
MIN_ROWS = 4
COL_ASPECT = 1.7              # a column unit is about this much wider than a row unit on a typical window
WEIGHT_FLOOR = 0.5             # tile weight = floor + importance / top importance, so the top tile is larger but no tile vanishes
TREND_BASE = 0.6               # a time view with no finding behind it scores this share of the top importance
MAX_FILTERS = 3                # category filters shown (the existing control caps at this many too)
OVERVIEW_LINE = "Overview of your data"
RANKED_TOOLS = ("group_compare", "pareto")      # finding charts that draw the same ranked bars as a category tile
TREND_TOOLS = ("trend",)


# ------------------------------------------------------------------ layout
def _cut(length: int, other: int, wa: float, wb: float, na: int, nb: int, side_min: int) -> int:
    """Where to cut a side of `length` units: in proportion to the weights, but leaving each half room for its tiles to stay readable."""
    need_a = max(min(side_min, length // 2), -(-na * MIN_TILE_AREA // other), 1)
    need_b = max(min(side_min, length // 2), -(-nb * MIN_TILE_AREA // other), 1)
    if need_a + need_b > length:                          # no room for everyone to be readable: share the length by tile count
        need_a = max(1, min(length - 1, round(length * na / (na + nb))))
        need_b = length - need_a
    return min(max(round(length * wa / (wa + wb)), need_a), length - need_b)


def layout(weights: list[float], cols: int = GRID_COLS, rows: int = GRID_ROWS) -> list[dict]:
    """Cut the grid into one rectangle per weight, in the order given (heaviest first reads best).
    Every cell belongs to exactly one rectangle.  Returns [{x, y, w, h}] in input order."""
    n = len(weights)
    out: list[dict | None] = [None] * n
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (-weights[i], i))

    def place(idx: list[int], x: int, y: int, w: int, h: int) -> None:
        if len(idx) == 1:
            out[idx[0]] = {"x": x, "y": y, "w": w, "h": h}
            return
        total = sum(weights[i] for i in idx) or float(len(idx))
        acc, best = 0.0, (None, 1)
        for k in range(1, len(idx)):                      # the split that balances the two halves best
            acc += weights[idx[k - 1]] or 1.0
            gap = abs(acc - total / 2)
            if best[0] is None or gap < best[0]:
                best = (gap, k)
        k = best[1]
        a, b = idx[:k], idx[k:]
        wa = sum(weights[i] or 1.0 for i in a)
        wb = sum(weights[i] or 1.0 for i in b)
        if w * COL_ASPECT >= h:                           # cut the longer side
            cut = _cut(w, h, wa, wb, len(a), len(b), MIN_COLS)
            place(a, x, y, cut, h)
            place(b, x + cut, y, w - cut, h)
        else:
            cut = _cut(h, w, wa, wb, len(a), len(b), MIN_ROWS)
            place(a, x, y, w, cut)
            place(b, x, y + cut, w, h - cut)

    place(order, 0, 0, cols, rows)
    return [r for r in out if r is not None]


def covered(rects: list[dict], cols: int = GRID_COLS, rows: int = GRID_ROWS) -> tuple[int, int]:
    """(cells covered at least once, cells covered more than once): a clean layout is (cols * rows, 0)."""
    seen: dict[tuple[int, int], int] = {}
    for r in rects:
        for i in range(r["x"], r["x"] + r["w"]):
            for j in range(r["y"], r["y"] + r["h"]):
                seen[(i, j)] = seen.get((i, j), 0) + 1
    return len(seen), sum(1 for v in seen.values() if v > 1)


def weights_for(tiles: list[dict]) -> list[float]:
    top = max((t["importance"] for t in tiles), default=0.0)
    return [WEIGHT_FLOOR + (max(t["importance"], 0.0) / top if top > 0 else 0.5) for t in tiles]


def arrange(tiles: list[dict]) -> dict:
    """Tiles (heaviest first) with their rectangles."""
    tiles = sorted(tiles, key=lambda t: (-t["importance"], t["id"]))
    rects = layout(weights_for(tiles))
    return {"cols": GRID_COLS, "rows": GRID_ROWS, "tiles": [{"id": t["id"], **r} for t, r in zip(tiles, rects)]}


# ------------------------------------------------------------------ the plan
def _finding_id(chart_id: str) -> str:
    return chart_id[3:] if chart_id.startswith("ch_") else chart_id


def build_plan(dash: dict, result: dict) -> dict:
    """Pick the dashboard's tiles, its "More" list and its filters from the saved selection."""
    cur = result.get("curation") or {}
    cands = {c["id"]: c for c in cur.get("candidates") or []}
    findings = {f["id"]: f for f in result.get("findings") or []}
    merges = cur.get("merges") or {}
    absorbed = set((cur.get("absorbed") or {}).keys())
    group = (cur.get("tabs") or {}).get("dashboard:chart") or {}
    overview = cur.get("mode") == "overview"
    top = max([c.get("importance", 0.0) for c in cands.values() if c.get("kind") in ("finding", "chart")] or [0.0])
    if top <= 0:                                           # an older run: score by the findings' own insight score
        top = max([(f.get("insight") or {}).get("score", 0.0) for f in findings.values()] or [0.0]) or 1.0
    measures = dash.get("measures") or []
    primary = measures[0]["col"] if measures else ""

    def imp_of(c: dict | None, f: dict | None) -> float:
        if c is not None and "importance" in c:
            return float(c["importance"])
        return float(((f or {}).get("insight") or {}).get("score") or 0.0)

    # --- the finding charts: the kept ones, then the ones the cut moved to "More"
    if cur and group:
        kept_ids, more_ids = list(group.get("kept") or []), list(group.get("more") or [])
    else:                                                  # no selection (older run): the gallery's own key order
        key = ((result.get("report_view") or {}).get("gallery") or {}).get("key") or []
        ranked = sorted((f for f in findings.values() if f.get("chart") and f.get("insight")),
                        key=lambda f: -((f.get("insight") or {}).get("score") or 0.0))
        kept_ids = [f"ch_{i}" for i in key if i in findings and findings[i].get("chart")]
        more_ids = [f"ch_{f['id']}" for f in ranked if f"ch_{f['id']}" not in kept_ids]

    fin_tiles, fin_more, covered_dims, covered_trend = [], [], {}, []
    for cid in kept_ids + more_ids:
        fid = _finding_id(cid)
        f = findings.get(fid)
        if not f or not f.get("chart") or cid in absorbed or fid in absorbed:
            continue
        c = cands.get(cid) or cands.get(fid)
        imp = imp_of(c, f)
        tile = {"id": f"chart:{fid}", "kind": "finding", "finding_id": fid, "importance": imp,
                "title": (f.get("insight") or {}).get("headline") or f.get("title", ""),
                "measure": (c or {}).get("measure", ""), "dimension": (c or {}).get("dimension", ""),
                "direct": bool((c or {}).get("direct")), "core": bool((c or {}).get("core"))}
        if merges.get(fid):
            tile["items"] = [{"measure": i.get("measure", ""), "text": i.get("text", "")} for i in merges[fid].get("items") or []]
        (fin_tiles if cid in kept_ids else fin_more).append(tile)

    # --- the interactive tiles
    live: list[dict] = []
    drawn_by_finding = lambda t: (t["finding_id"] and findings.get(t["finding_id"], {}).get("tool"))   # noqa: E731
    if dash.get("date"):
        trend_imp = max([t["importance"] for t in fin_tiles + fin_more if drawn_by_finding(t) in TREND_TOOLS and t["measure"] == primary] or [top * TREND_BASE])
        live.append({"id": "trend", "kind": "trend", "importance": trend_imp, "core": overview, "direct": False, "title": "", "finding_id": ""})
    for d in dash.get("dims") or []:
        col = d["col"]
        match = [t["importance"] for t in fin_tiles + fin_more if t["dimension"] == col and t["measure"] == primary and drawn_by_finding(t) in RANKED_TOOLS]
        imp = max(match) if match else top * float(d.get("score") or 0.0)
        live.append({"id": f"dim:{col}", "kind": "dim", "col": col, "importance": imp, "core": bool(d.get("top")), "direct": False, "title": "", "finding_id": ""})
        covered_dims[col] = True

    # a finding chart that draws what a live tile already draws is left out (never two of the same chart)
    def duplicate(t: dict) -> bool:
        tool = drawn_by_finding(t)
        if tool in TREND_TOOLS and t["measure"] == primary and dash.get("date"):
            return True
        return tool in RANKED_TOOLS and t["measure"] == primary and t["dimension"] in covered_dims
    fin_tiles = [t for t in fin_tiles if not duplicate(t)]
    fin_more = [t for t in fin_more if not duplicate(t)]

    # --- the cut, then what one screen holds
    pool = live + fin_tiles
    kept, more, _ = curate.apply_cut([{**t, "null_result": False, "caution": False, "pinned": False} for t in pool])
    kept = sorted(kept, key=lambda t: (-t["importance"], t["id"]))
    spill, kept = kept[MAX_TILES:], kept[:MAX_TILES]
    more_all = sorted(more + spill, key=lambda t: (-t["importance"], t["id"])) + fin_more
    slim = lambda t: {k: v for k, v in t.items() if k not in ("null_result", "caution", "pinned")}   # noqa: E731
    kept, more_all = [slim(t) for t in kept], [slim(t) for t in more_all]

    # --- filters: the category columns tied most closely to the chosen tiles, then the date controls
    scored = []
    for d in dash.get("dims") or []:
        col = d["col"]
        tied = max([t["importance"] for t in kept + more_all if t.get("dimension") == col or t.get("col") == col] or [0.0])
        in_view = any(t.get("col") == col or t.get("dimension") == col for t in kept)
        scored.append((not in_view, -tied, -float(d.get("score") or 0.0), col))
    scored.sort()
    filters = [s[3] for s in scored[:MAX_FILTERS]]

    return {"curated": bool(cur), "tiles": kept, "more": more_all, "filters": filters, "layout": arrange(kept),
            "objective": objective_line(result), "mode": "overview" if overview else "goal"}


def objective_line(result: dict) -> str:
    """The line under the dashboard title: what the person asked, or a plain overview line for an explore run.  Never blank, never a placeholder."""
    cur = result.get("curation") or {}
    plan = result.get("plan") or {}
    ctx = result.get("context") or {}
    if cur.get("mode") == "overview" or plan.get("explore") or ctx.get("explore"):
        return OVERVIEW_LINE
    text = str(plan.get("objective") or result.get("goal") or "").strip()
    return text or OVERVIEW_LINE
