"""End-to-end text scan: run the whole pipeline (fake AI) on two differently shaped files and read every piece of text the app shows a person.
No raw column name, no total-versus-total drop for an unfinished last period, no two charts that show the same thing, a one-to-one pair of columns only once.
Every failure names the string and where it came from. Nothing here is specific to a file: the column names are read from the data."""
import json
import os
import re

os.environ["LLM_PROVIDER"] = "none"

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app import runner, viewedit  # noqa: E402
from app import understanding as und  # noqa: E402
from app.dashboard import build_dashboard  # noqa: E402
from tests.fixtures_bakery import bakery, boutique  # noqa: E402

# keys whose value is an internal handle (an id, a parameter, a file name), not words shown to a person
HANDLES = {"id", "ids", "tool", "chart", "charts", "file", "path", "src", "image", "params", "param", "group", "target", "measure", "dimension", "dim", "col", "column", "columns", "date",
           "date_label", "kind", "key", "role", "type", "finding_id", "source", "fact_id", "facts", "den", "num", "name", "x", "y", "by", "metric", "sort", "tab", "tabs", "mode", "level",
           "rows", "unit", "fmt", "format", "chart_id", "ref", "refs", "cols", "label_col", "measure_col", "dimension_col", "kept", "more", "kept_ids", "more_ids", "absorbed", "robust_map",
           "currency", "name_col", "stat", "op", "scope", "status", "direction", "polarity", "verdict", "grain", "seed"}


def _walk(obj, where, out, skip=HANDLES):
    if isinstance(obj, str):
        out.append((where, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip:
                _walk(v, f"{where}.{k}", out, skip)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _walk(v, f"{where}[{i}]", out, skip)


def _run(tmp_path, monkeypatch, df, name):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("GLOSSARY_PATH", str(tmp_path / "g.json"))
    rid = runner.create_run("", [(name, df.to_csv(index=False).encode())])
    runner._execute(rid)
    wd = runner.run_dir(rid)
    if (runner.read_status(rid) or {}).get("status") == "needs_input":
        (wd / "answers.json").write_text(json.dumps({"answers": {}, "skip": True, "notes": ""}))
        runner._execute(rid)
    assert (runner.read_status(rid) or {}).get("status") == "done"
    return wd, json.loads((wd / "result.json").read_text(encoding="utf-8"))


def collect_text(wd, res):
    """Every (source, text) the person can read: the report, its tabs, chart titles, merged-card lines, the dashboard plan, the Lens item titles, the print text and the Power BI guide.
    The page puts display names into text the analysis wrote (display.ts humanise); `und.readable` is its Python twin, so text from those fields is scanned as the page would show it.
    summary_facts and the curation candidates are the app's own working records (the page shows them only through report_view and the catalogue), so they are not scanned."""
    shown = lambda t: und.readable(t, res) if isinstance(t, str) else t      # noqa: E731
    out = [("report.md (the printed / PDF text)", res["report_md"])]
    page = []
    for key in ("report_view", "strategies", "strategy_summary", "goal_answer", "key_metrics", "data_gaps", "decisions"):
        _walk(res.get(key), key, page)
    for f in res.get("findings") or []:
        fid = f.get("id", "?")
        for k in ("title", "caption", "headline"):
            if isinstance(f.get(k), str):
                page.append((f"findings[{fid}].{k} (chart title)", f[k]))
        _walk(f.get("insight"), f"findings[{fid}].insight", page)
    out += [(w, shown(t)) for w, t in page]
    cur = res.get("curation") or {}
    for cid, m in (cur.get("merges") or {}).items():
        for i, item in enumerate(m.get("items") or []):
            if isinstance(item.get("text"), str):
                out.append((f"curation.merges[{cid}].items[{i}].text (merged card line)", item["text"]))
        _walk({k: v for k, v in m.items() if k != "items"}, f"curation.merges[{cid}]", out)
    for iid, it in viewedit.catalogue(res)["items"].items():
        out.append((f"Lens item title {iid}", it["title"]))
    spec = build_dashboard(wd, res, [])
    plan = spec["plan"]
    out.append(("dashboard objective (key figures)", shown(spec["objective"])))
    dash = []
    _walk({k: v for k, v in plan.items() if k not in ("layout", "filters")}, "dashboard plan", dash)
    dash += [(f"dashboard filter {c}", c) for c in plan.get("filters") or []]
    for m in spec["measures"]:
        dash.append((f"dashboard key figure {m.get('col')}", m.get("label") or ""))
    out += [(w, shown(t)) for w, t in dash]
    guide = wd / "powerbi" / "POWERBI_GUIDE.md"
    assert not [w for w in res.get("warnings") or [] if "stage failed" in w], res["warnings"]      # a stage that failed silently would leave nothing to scan
    assert guide.exists() and (wd / "powerbi" / "visuals.json").exists()
    if True:
        text = guide.read_text(encoding="utf-8")
        out.append(("powerbi/POWERBI_GUIDE.md", re.sub(r"## Field names in Power BI.*?(?=\n## )", "", text, flags=re.S)))      # the one table that lists the exact field names, on purpose
    vis = wd / "powerbi" / "visuals.json"
    if vis.exists():
        _walk(json.loads(vis.read_text(encoding="utf-8")), "powerbi/visuals.json", out, HANDLES | {"field", "fields", "dax"})
    return out, spec


def find_raw_names(texts, df):
    """Anything with an underscore, and a name squashed (spaces and underscores removed, lower case) that no longer reads as the plain words."""
    cols = [str(c) for c in df.columns]
    under = [c for c in cols if "_" in c]
    squashed = {re.sub(r"[\s_]+", "", c).lower() for c in cols if "_" in c or " " in c}
    rx = re.compile("(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, under)) + ")(?![A-Za-z0-9_])") if under else None
    sq = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, sorted(squashed))) + r")(?![A-Za-z0-9])") if squashed else None
    bad = []
    for where, text in texts:
        text = re.sub(r"\]\(charts/[^)]*\)", "]()", text)       # image links keep file names
        if re.fullmatch(r"charts/[\w./-]+\.png", text.strip()):
            continue
        bad += [f"{where}: raw column name {m!r} in {text[:140]!r}" for m in (rx.findall(text) if rx else [])]
        bad += [f"{where}: squashed column name {m!r} in {text[:140]!r}" for m in (sq.findall(text.lower()) if sq else [])]
    return bad


DROP = re.compile(r"(\bfell\b|\bdrop(ped)?\b|\bdown\b|\bdecline[ds]?\b|\bshrank\b|\blower\b|[-−]\d+(\.\d+)?%)", re.I)
HEDGE = re.compile(r"per day|a day|partial|incomplete|so far|part-|part of|too early|days? of", re.I)


def find_part_period_drops(texts, last_date):
    """The last period is part-finished: a line about it that reads as a fall must say it is per day / partial."""
    last = pd.Timestamp(last_date)
    month, ym = last.strftime("%B").lower(), last.strftime("%Y-%m")
    bad = []
    for where, text in texts:
        for sent in re.split(r"(?<=[.!?])\s+|\n", text):
            if (month in sent.lower() or ym in sent) and DROP.search(sent) and not HEDGE.search(sent):
                bad.append(f"{where}: unqualified drop for the unfinished last period: {sent.strip()[:200]!r}")
    return bad


def find_duplicate_charts(spec):
    """Two tiles (shown or under More charts) with the same chart type, measure and dimension."""
    bad, seen = [], {}
    for t in spec["plan"]["tiles"] + spec["plan"]["more"]:
        sig = (t.get("kind"), t.get("chart_type") or t.get("type"), t.get("measure"), t.get("col") or t.get("dimension") or t.get("dim"), t.get("finding_id"))
        if sig in seen:
            bad.append(f"dashboard plan: tiles {seen[sig]!r} and {t.get('id')!r} are the same chart {sig}")
        seen.setdefault(sig, t.get("id"))
    return bad


def find_twin_columns(df, spec):
    """Two columns that group the rows identically (one value of one goes with exactly one of the other) appear once, as dimensions, filters and charts."""
    pools = {"dimensions": [d["col"] for d in spec["dims"]],
             "filters": [f if isinstance(f, str) else f.get("col") for f in spec["plan"].get("filters") or []],
             "chart tiles": [t.get("col") for t in spec["plan"]["tiles"] + spec["plan"]["more"] if t.get("kind") == "dim"]}
    bad = []
    for label, pool in pools.items():
        for i, a in enumerate(pool):
            for b in pool[i + 1:]:
                if a in df.columns and b in df.columns and a != b:
                    g = df[[a, b]].dropna().astype(str).drop_duplicates()
                    if len(g) > 1 and g[a].nunique() == len(g) == g[b].nunique():
                        bad.append(f"dashboard {label}: {a!r} and {b!r} group the rows identically but both appear")
    return bad


@pytest.mark.parametrize("make", [bakery, boutique], ids=["bakery", "boutique"])
def test_no_text_the_app_shows_breaks_the_rules(tmp_path, monkeypatch, make):
    df = make()
    wd, res = _run(tmp_path, monkeypatch, df, "data.csv")
    texts, spec = collect_text(wd, res)
    assert len(texts) > 40, "the scan found almost no text: the collector is broken"
    guide = (wd / "powerbi" / "POWERBI_GUIDE.md").read_text(encoding="utf-8")
    assert "## Field names in Power BI" in guide and "`net_received`" in guide or "`gross_amount`" in guide or "`amount`" in guide      # the exact field names are still given, once
    problems = find_raw_names(texts, df)
    if pd.Timestamp(df["order_date"].max()).day < 25:
        problems += find_part_period_drops(texts, df["order_date"].max())
    problems += find_duplicate_charts(spec)
    problems += find_twin_columns(df, spec)
    assert not problems, f"{len(problems)} problem(s):\n" + "\n".join(problems)


def test_the_detectors_catch_what_they_are_for():
    df = pd.DataFrame({"net_received": [1], "Gross Amount": [1], "order_date": ["2025-10-01"], "item": ["a"], "item_code": ["x"]})
    bad = find_raw_names([("a", "Top by net_received"), ("b", "Total grossamount rose"), ("c", "Net received is fine"), ("d", "![x](charts/net_received.png)")], df)
    assert len(bad) == 2 and "net_received" in bad[0] and "grossamount" in bad[1]
    drops = find_part_period_drops([("k", "Revenue in October fell 60% against September. Revenue in October is 20% lower per day."), ("l", "September fell 5%.")], "2025-10-09")
    assert len(drops) == 1 and "k:" in drops[0]
    tile = lambda i, **kw: {"id": i, "kind": "dim", "col": "item", **kw}      # noqa: E731
    assert find_duplicate_charts({"plan": {"tiles": [tile("a")], "more": [tile("b")]}}) and not find_duplicate_charts({"plan": {"tiles": [tile("a")], "more": [tile("b", col="other")]}})
    spec = {"dims": [{"col": "item"}, {"col": "item_code"}], "plan": {"tiles": [], "more": [], "filters": ["item", "item_code"]}}
    pair = pd.DataFrame({"item": ["a", "b", "c"], "item_code": ["x", "y", "z"]})
    assert len(find_twin_columns(pair, spec)) == 2 and not find_twin_columns(pd.DataFrame({"item": ["a", "a", "b"], "item_code": ["x", "y", "z"]}), spec)
