"""Power BI export pack: cleaned data + data dictionary + DAX measures + visual specs + theme + how-to.

Power BI Desktop is Windows-only and its .pbix format is proprietary, so instead of faking a .pbix we produce
everything needed to build the dashboard in a few minutes (Get Data > Text/CSV, paste measures, build visuals)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd

from .measures import has_sum_word
from .tools import to_binary

TABLE = "Data"
THEME = {"name": "Analyst Studio", "dataColors": ["#4F6BED", "#22A699", "#F2994A", "#E5484D", "#8E6BEE", "#3FA9F5", "#A3A847", "#C45AB3"],
         "background": "#FFFFFF", "foreground": "#1F2937", "tableAccent": "#4F6BED"}


def _col(c: str) -> str:
    return f"'{TABLE}'[{c}]"


def build_measures(df: pd.DataFrame, plan: dict, roles: dict, kinds: dict | None = None) -> list[dict]:
    m = [{"name": "Row Count", "dax": f"Row Count = COUNTROWS('{TABLE}')", "note": "Number of records."}]
    t, date = plan.get("target"), plan.get("date_col")
    groups = [a["params"]["group"] for a in plan.get("analyses", []) if "group" in a["params"]]
    groups = list(dict.fromkeys(groups))
    if t:
        if roles.get(t) == "binary":
            b, pos = to_binary(df[t])
            lit = f'"{pos}"' if isinstance(pos, str) else str(pos).lower() if isinstance(pos, bool) else str(pos)
            m.append({"name": f"{t} Rate", "dax": f"{t} Rate = DIVIDE(CALCULATE(COUNTROWS('{TABLE}'), {_col(t)} = {lit}), COUNTROWS('{TABLE}'))",
                      "note": f"Share of rows where {t} = {pos}. Format as percentage."})
            base = f"[{t} Rate]"
        elif t in {d["name"] for d in plan.get("derived") or []}:
            d = next(d for d in plan["derived"] if d["name"] == t)      # a ratio is total A over total B, never an average of row ratios
            m.append({"name": t, "dax": f"{t} = DIVIDE(SUM({_col(d['a'])}), SUM({_col(d['b'])}))", "note": f"Total {d['a']} over total {d['b']}. Format as {'percentage' if d.get('part_of') else 'a plain number'}."})
            base = f"[{t}]"
        else:
            use_sum = has_sum_word(t, overrides=kinds)
            m.append({"name": f"Total {t}", "dax": f"Total {t} = SUM({_col(t)})", "note": ""})
            m.append({"name": f"Average {t}", "dax": f"Average {t} = AVERAGE({_col(t)})", "note": ""})
            m.append({"name": f"Median {t}", "dax": f"Median {t} = MEDIAN({_col(t)})", "note": "Better than average when the column is skewed."})
            base = f"[Total {t}]" if use_sum else f"[Average {t}]"
            for g in groups[:2]:
                if use_sum:
                    m.append({"name": f"Share of {t} by {g}", "dax": f"Share of {t} by {g} = DIVIDE([Total {t}], CALCULATE([Total {t}], ALL({_col(g)})))",
                              "note": "Use with a bar chart on this column to reproduce the Pareto view."})
        if date:
            m.append({"name": f"{t} Prior Month", "dax": f"{t} Prior Month = CALCULATE({base}, DATEADD({_col(date)}, -1, MONTH))",
                      "note": "Needs a continuous date column; mark a Date table if you add one."})
            m.append({"name": f"{t} MoM %", "dax": f"{t} MoM % = DIVIDE({base} - [{t} Prior Month], [{t} Prior Month])", "note": "Format as percentage."})
            if roles.get(t) != "binary" and has_sum_word(t, overrides=kinds):
                m.append({"name": f"{t} YTD", "dax": f"{t} YTD = TOTALYTD({base}, {_col(date)})", "note": ""})
    return m


def build_visuals(findings: list[dict], plan: dict) -> list[dict]:
    t = plan.get("target")
    out = []
    for f in findings:
        p, tool = f.get("params", {}), f["tool"]
        if tool == "group_compare":
            out.append({"visual": "Clustered bar chart", "from": f["title"], "axis": p["group"], "values": f"Average/Rate of {t}", "tip": "Sort descending; add a slicer on other categories."})
        elif tool == "heatmap":
            out.append({"visual": "Matrix with conditional formatting", "from": f["title"], "axis": f"Rows: {p['group_a']}; Columns: {p['group_b']}", "values": f"Average/Rate of {t}",
                        "tip": "Format > Cell elements > Background color > Gradient, to reproduce the heatmap."})
        elif tool == "segment_trend":
            out.append({"visual": "Line chart with legend", "from": f["title"], "axis": f"{p['date']} (Month)", "values": f"{t}, legend = {p['group']}", "tip": "Show only the top few groups so the lines stay readable."})
        elif tool == "outcome_compare":
            out.append({"visual": "Clustered column chart", "from": f["title"], "axis": t, "values": f"Average of {p['col']}", "tip": "Compare the average of the driver for each outcome value."})
        elif tool == "pareto":
            out.append({"visual": "Column chart + line (Pareto)", "from": f["title"], "axis": p["group"], "values": f"Total {t} + cumulative %", "tip": "Use 'Show value as > Running total %' for the line."})
        elif tool == "trend":
            out.append({"visual": "Line chart", "from": f["title"], "axis": f"{p['date']} (Month)", "values": f"{t}", "tip": "Turn on Analytics > Trend line."})
        elif tool == "correlation":
            out.append({"visual": "Scatter chart", "from": f["title"], "axis": "Two strongly related numeric columns", "values": "-", "tip": "Add a trend line from the Analytics pane."})
        elif tool == "scatter":
            out.append({"visual": "Scatter chart", "from": f["title"], "axis": p["x"], "values": p["y"], "tip": "Add a trend line from the Analytics pane; put a categorical column in Legend to check segments."})
        elif tool == "composition":
            out.append({"visual": "100% stacked bar chart", "from": f["title"], "axis": p["group"], "values": f"Count, legend = {p['by']}", "tip": "Turn on data labels to show the percentages."})
        elif tool == "seasonality":
            out.append({"visual": "Column chart by weekday / month", "from": f["title"], "axis": f"{p['date']} (Day of week or Month name)", "values": p["target"], "tip": "Add the date hierarchy and drill to Day or Month; add an average line from Analytics."})
        elif tool in ("summary_table", "group_table"):
            out.append({"visual": "Table", "from": f["title"], "axis": p.get("group", "Numeric columns"), "values": "Rows, average, total", "tip": "Use conditional formatting data bars on the value columns."})
        elif tool == "distribution":
            out.append({"visual": "Histogram (column chart on binned field)", "from": f["title"], "axis": f"{p['col']} (bins)", "values": "Row Count", "tip": "Create bins via New group > Bin."})
    out.insert(0, {"visual": "KPI cards", "from": "Overview", "axis": "-", "values": "Row Count" + (f", main {t} measure" if t else ""), "tip": "Place at the top of the page."})
    return out


def build_pack(workdir: Path, df: pd.DataFrame, profile: dict, plan: dict, findings: list[dict], goal: str, data_model: dict | None = None, kinds: dict | None = None) -> dict:
    pack = workdir / "powerbi"
    pack.mkdir(exist_ok=True)
    roles = {c["name"]: c["role"] for c in profile["columns"]}

    model_md = ""
    if data_model:
        # Power BI can model the original tables natively: export them plus the relationships found.
        (pack / "tables").mkdir(exist_ok=True)
        for t in data_model["tables"]:
            pd.read_pickle(workdir / f"tbl_{t['name']}.pkl").to_csv(pack / "tables" / f"{t['name']}.csv", index=False, encoding="utf-8-sig")
        used = [r for r in data_model["relationships"] if r["status"] == "used" and r["method"] == "join"] + \
               [r for r in data_model["relationships"] if r["status"] == "used" and r["method"] == "aggregate"]
        pd.DataFrame([{"from_table": r["child_table"], "from_column": r["child_col"], "to_table": r["parent_table"], "to_column": r["parent_col"],
                       "cardinality": "One-to-one" if r["cardinality"] == "one-to-one" else "Many-to-one", "cross_filter_direction": "Single",
                       "match_rate": r["child_row_match"]} for r in used]).to_csv(pack / "relationships.csv", index=False)
        model_md = f"""
## Alternative: a proper star-schema model (recommended for reuse)
The flat `cleaned_data.csv` already combines your tables (one row per `{data_model['base_table']}`). If you prefer to model them natively in Power BI:
1. Get data > Text/CSV for every file in `tables/`.
2. Model view > Manage relationships > New, one per row of `relationships.csv` (many-to-one from `from_table.from_column` to `to_table.to_column`, single direction).
3. In `measures.dax`, replace the table name `'{TABLE}'` with your fact table (`{data_model['base_table']}`).
"""

    df.to_csv(pack / "cleaned_data.csv", index=False, encoding="utf-8-sig")
    try:
        df.to_parquet(pack / "cleaned_data.parquet", index=False)
    except Exception:
        pass
    dd = pd.DataFrame([{"column": c["name"], "detected_role": c["role"], "recommended_power_bi_type": {
        "numeric": "Decimal number", "binary": "Text / True-False", "categorical": "Text", "date": "Date", "id": "Text (hide from report view)", "text": "Text"}.get(c["role"], "Text"),
        "missing_pct": c["missing_pct"], "unique_values": c["unique"], "used_in_analysis": c["name"] in plan.get("relevant_columns", [])} for c in profile["columns"]])
    dd.to_csv(pack / "data_dictionary.csv", index=False)

    measures = build_measures(df, plan, roles, kinds)
    (pack / "measures.dax").write_text("\n\n".join(f"// {x['note']}\n{x['dax']}" if x["note"] else x["dax"] for x in measures) + "\n", encoding="utf-8")
    visuals = build_visuals(findings, plan)
    (pack / "visuals.json").write_text(json.dumps(visuals, indent=2), encoding="utf-8")
    (pack / "theme.json").write_text(json.dumps(THEME, indent=2), encoding="utf-8")

    vis_md = "\n".join(f"| {v['visual']} | {v['axis']} | {v['values']} | {v['tip']} |" for v in visuals)
    guide = f"""# Build the dashboard in Power BI Desktop

**Goal:** {goal}

## 1. Load the data (2 min)
1. Home > Get data > Text/CSV > `cleaned_data.csv` > Load. (Or `cleaned_data.parquet` via Get data > Parquet.)
2. Check column types against `data_dictionary.csv` (column `recommended_power_bi_type`). Hide ID columns.
3. Optional: rename the table to **{TABLE}** (the measures assume this name).

## 2. Add the measures (3 min)
Model view > New measure, then paste each line from `measures.dax` (one measure per entry).

## 3. Apply the theme
View > Themes > Browse for themes > `theme.json`.

## 4. Build the visuals (what the analysis found)
| Visual | Axis / Category | Values | Tip |
|---|---|---|---|
{vis_md}

## 5. Tell the story
Page title = the goal. Put KPI cards on top, then the trend, then the segment comparisons. Add slicers for the grouping columns used in analysis.
Use the written findings in `report.md` as the text boxes/captions for each visual.
{model_md}"""
    (pack / "POWERBI_GUIDE.md").write_text(guide, encoding="utf-8")

    zip_path = workdir / "powerbi_pack.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pack.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(pack).as_posix())
        rep = workdir / "report.md"
        if rep.exists():
            z.write(rep, "report.md")
        charts = workdir / "charts"
        if charts.exists():
            for ch in sorted(charts.glob("*.png")):
                z.write(ch, f"charts/{ch.name}")
    return {"zip": "powerbi_pack.zip", "files": sorted(p.relative_to(pack).as_posix() for p in pack.rglob("*") if p.is_file()), "measures": [x["dax"] for x in measures], "visuals": visuals}
