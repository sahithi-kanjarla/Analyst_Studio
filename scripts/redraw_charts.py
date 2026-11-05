"""Draw a finished run's chart pictures again, without re-running the analysis.

The figures, the wording and the trust labels of a finished run are in result.json and are not
touched: only the PNG files are replaced. Use it when the palette changes (the colours live in one
place, frontend/src/tokens.css, and scripts/gen_palette.py carries them into the code that draws),
so the run shipped with the repo does not keep pictures in the old colours.

    python scripts/redraw_charts.py sample_data/demo/sales_run

It replays each finding's own tool with the parameters the run recorded, and redraws the charts of
the report page from the same specs the reporter offered. A picture whose file is not already in
the run is not drawn, so the run never gains a chart it did not have.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("LLM_PROVIDER", "none")

import pandas as pd  # noqa: E402

from app import brief as briefmod  # noqa: E402
from app import mainview, reportcharts, tools  # noqa: E402
from app import understanding as undmod  # noqa: E402


def redraw(run: Path) -> tuple[int, int, list[str]]:
    """(findings redrawn, report charts redrawn, what could not be drawn)."""
    state = json.loads((run / "result.json").read_text(encoding="utf-8"))
    state["workdir"] = str(run)
    charts = run / "charts"
    df = pd.read_pickle(run / "clean.pkl")
    ctx = {"charts_dir": charts, "currency": (state.get("context") or {}).get("currency", ""),
           "kinds": briefmod.kinds(state.get("brief")), "money": undmod.money_columns(state.get("understanding")),
           "derived": {d["name"]: d for d in (state.get("plan") or {}).get("derived") or []},
           "display_names": undmod.display_names(state)}
    problems: list[str] = []

    done = 0
    for f in state.get("findings") or []:
        want = f.get("chart")
        if not want or not (run / want).exists():
            continue
        fn = tools.REGISTRY.get(f["tool"])
        if not fn:
            problems.append(f"{f['id']}: no tool named {f['tool']}")
            continue
        try:
            out = fn(df, ctx, **(f.get("params") or {}))
        except Exception as e:                     # a tool that cannot run again leaves its old picture
            problems.append(f"{f['id']} {f['tool']}: {type(e).__name__}: {e}")
            continue
        if out is None or out.get("chart") != want:
            problems.append(f"{f['id']} {f['tool']}: drew {out and out.get('chart')}, run holds {want}")
            continue
        done += 1

    page = 0
    view = state.get("report_view") or {}
    try:
        by_id, r_by_id, _ = mainview.lookup(state, view)
    except Exception as e:
        problems.append(f"report page: {type(e).__name__}: {e}")
        by_id, r_by_id = {}, {}
    for sp in reportcharts.specs_for([*by_id, *r_by_id], by_id, r_by_id, state):
        if (run / reportcharts.draw_path(sp)).exists():
            reportcharts.draw(sp, charts)
            page += 1
    return done, page, problems


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    run = Path(argv[0]) if Path(argv[0]).is_absolute() else ROOT / argv[0]
    if not (run / "result.json").exists():
        print(f"not a finished run: {run}")
        return 2
    done, page, problems = redraw(run)
    print(f"{run.name}: redrew {done} finding charts and {page} report-page charts")
    for p in problems:
        print(f"  could not redraw: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
