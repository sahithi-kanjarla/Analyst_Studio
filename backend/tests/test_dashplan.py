"""The dashboard plan: tile sizes that fill the window, no duplicate chart, bigger tiles when few things matter, the objective line."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import dashplan
from app.main import app


def _tiles(n, start=10.0, step=0.9):
    return [{"id": f"t{i}", "importance": start - i * step} for i in range(n)]


@pytest.mark.parametrize("n", [1, 2, 3, 6, 10, dashplan.MAX_TILES])
def test_tiles_cover_the_grid_with_no_gap_and_no_overlap(n):
    arr = dashplan.arrange(_tiles(n))
    assert len(arr["tiles"]) == n
    cells, doubled = dashplan.covered(arr["tiles"])
    assert cells == dashplan.GRID_COLS * dashplan.GRID_ROWS and doubled == 0
    for t in arr["tiles"]:
        assert t["w"] >= 1 and t["h"] >= 1 and t["x"] >= 0 and t["y"] >= 0
        assert t["x"] + t["w"] <= dashplan.GRID_COLS and t["y"] + t["h"] <= dashplan.GRID_ROWS


def test_the_most_important_tile_is_the_largest():
    arr = dashplan.arrange([{"id": "small", "importance": 2.0}, {"id": "big", "importance": 10.0}, {"id": "mid", "importance": 4.0}])
    area = {t["id"]: t["w"] * t["h"] for t in arr["tiles"]}
    assert area["big"] == max(area.values()) and area["big"] > area["small"]


def test_fewer_tiles_are_bigger_not_padded():
    three = dashplan.arrange(_tiles(3))["tiles"]
    ten = dashplan.arrange(_tiles(10))["tiles"]
    assert min(t["w"] * t["h"] for t in three) > max(t["w"] * t["h"] for t in ten)
    assert sum(t["w"] * t["h"] for t in three) == sum(t["w"] * t["h"] for t in ten)        # same screen, fewer and bigger tiles


def _finding(fid, tool, measure, dim, score, chart=True):
    params = {"target": measure, **({"group": dim} if dim else {})}
    return {"id": fid, "tool": tool, "chart": f"{fid}.png" if chart else None, "params": params, "title": f"{measure} {fid}",
            "insight": {"headline": f"{measure} headline {fid}", "score": score, "confidence": {"level": "high"}}}


def _result(findings, kept, more=(), mode="goal", objective="Which group sells most?", merges=None, explore=False):
    cands = [{"id": f"ch_{f['id']}", "kind": "chart", "measure": f["params"]["target"], "dimension": f["params"].get("group", ""),
              "importance": f["insight"]["score"]} for f in findings]
    return {"findings": findings, "goal": "" if explore else objective, "plan": {"objective": objective, "explore": explore},
            "curation": {"candidates": cands, "mode": mode, "merges": merges or {}, "absorbed": {},
                         "tabs": {"dashboard:chart": {"kept": [f"ch_{k}" for k in kept], "more": [f"ch_{k}" for k in more]}}}}


def _dash(dims=("region",), date=True, measure="revenue"):
    return {"measures": [{"col": measure}], "date": "__date" if date else None,
            "dims": [{"col": d, "unique": 4, "score": 0.3} for d in dims]}


def test_a_finding_chart_that_repeats_a_live_chart_is_never_a_second_tile():
    fs = [_finding("a", "group_compare", "revenue", "region", 9.0), _finding("b", "trend", "revenue", "", 8.0),
          _finding("c", "group_compare", "revenue", "channel", 7.0)]
    plan = dashplan.build_plan(_dash(dims=("region",)), _result(fs, ["a", "b", "c"]))
    ids = [t["id"] for t in plan["tiles"]] + [t["id"] for t in plan["more"]]
    assert len(ids) == len(set(ids))
    assert "chart:a" not in ids and "chart:b" not in ids          # the region bars and the time view are already drawn live
    assert "chart:c" in ids and "dim:region" in ids and "trend" in ids
    kinds = [(t.get("kind"), t.get("col") or t.get("finding_id")) for t in plan["tiles"] + plan["more"]]
    assert len(kinds) == len(set(kinds))


def test_few_important_items_give_few_tiles_and_nothing_is_added_as_filler():
    fs = [_finding("a", "group_compare", "revenue", "channel", 9.0)]
    weak = _dash(dims=("region",), date=False)
    weak["dims"][0]["score"] = 0.01
    plan = dashplan.build_plan(weak, _result(fs, ["a"]))
    assert 1 <= len(plan["tiles"]) <= 3
    cells, doubled = dashplan.covered(plan["layout"]["tiles"])
    assert (cells, doubled) == (dashplan.GRID_COLS * dashplan.GRID_ROWS, 0)
    assert len(plan["layout"]["tiles"]) == len(plan["tiles"])        # one rectangle per tile: no empty slot, no extra chart
    biggest = max(t["w"] * t["h"] for t in plan["layout"]["tiles"])
    assert biggest >= dashplan.GRID_COLS * dashplan.GRID_ROWS // 3


def test_a_merged_finding_is_one_tile_with_its_measures():
    fs = [_finding("a", "group_compare", "revenue", "channel", 9.0), _finding("b", "group_compare", "units", "channel", 8.5)]
    merges = {"a": {"items": [{"measure": "revenue", "text": "revenue: web at 100"}, {"measure": "units", "text": "units: web at 5"}]}}
    res = _result(fs, ["a"], merges=merges)
    res["curation"]["absorbed"] = {"b": "a", "ch_b": "ch_a"}
    plan = dashplan.build_plan(_dash(dims=(), date=False), res)
    tile = [t for t in plan["tiles"] if t["id"] == "chart:a"][0]
    assert [i["measure"] for i in tile["items"]] == ["revenue", "units"]
    assert not [t for t in plan["tiles"] + plan["more"] if t["id"] == "chart:b"]


def test_the_objective_line_is_sensible_for_an_explore_run():
    res = _result([], [], mode="overview", objective="Explore the data and surface the most important patterns (no specific goal was given).", explore=True)
    assert dashplan.objective_line(res) == dashplan.OVERVIEW_LINE
    res = {"plan": {"explore": True, "objective": ""}, "goal": ""}
    assert dashplan.objective_line(res) == dashplan.OVERVIEW_LINE
    assert dashplan.objective_line({"plan": {"objective": "Which group sells most?"}, "goal": "x", "curation": {"mode": "goal"}}) == "Which group sells most?"
    assert dashplan.objective_line({"plan": {}, "goal": ""}) == dashplan.OVERVIEW_LINE          # never blank


def test_filters_are_few_and_follow_the_chosen_tiles():
    fs = [_finding("a", "group_compare", "revenue", "channel", 9.0)]
    d = _dash(dims=("region", "channel", "tier", "size"), date=False)
    plan = dashplan.build_plan(d, _result(fs, ["a"]))
    assert 1 <= len(plan["filters"]) <= dashplan.MAX_FILTERS and set(plan["filters"]) <= {x["col"] for x in d["dims"]}


def test_layout_endpoint_is_a_pure_function_of_the_tiles():
    from test_new_tools_and_views import run, sales
    rid, _ = run(sales(), "What drives revenue?")
    c = TestClient(app)
    r = c.get(f"/api/runs/{rid}/dashboard/layout", params={"tiles": "a:9,b:5,c:2"})
    assert r.status_code == 200
    assert dashplan.covered(r.json()["tiles"]) == (dashplan.GRID_COLS * dashplan.GRID_ROWS, 0)
    assert c.get(f"/api/runs/{rid}/dashboard/layout", params={"tiles": "a:x"}).status_code == 400


def test_a_real_run_has_a_plan_that_fills_the_grid():
    from test_new_tools_and_views import run, sales
    rid, _ = run(sales(), "What drives revenue?")
    d = TestClient(app).get(f"/api/runs/{rid}/dashboard").json()
    plan = d["plan"]
    ids = [t["id"] for t in plan["tiles"]]
    assert ids and len(ids) == len(set(ids)) and len(ids) <= dashplan.MAX_TILES
    assert dashplan.covered(plan["layout"]["tiles"]) == (dashplan.GRID_COLS * dashplan.GRID_ROWS, 0)
    assert {t["id"] for t in plan["layout"]["tiles"]} == set(ids)
    assert set(plan["filters"]) <= {x["col"] for x in d["dims"]} and len(plan["filters"]) <= dashplan.MAX_FILTERS
    assert d["objective"].strip()
