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
    r = c.get(f"/api/runs/{rid}/dashboard/layout", params={"importance": "9,5,2"})
    assert r.status_code == 200
    assert dashplan.covered(r.json()["tiles"]) == (dashplan.GRID_COLS * dashplan.GRID_ROWS, 0)
    assert c.get(f"/api/runs/{rid}/dashboard/layout", params={"importance": "x"}).status_code == 400


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


def test_merged_measures_stay_visible_when_the_chart_is_drawn_live():
    fs = [_finding("a", "group_compare", "revenue", "region", 9.0), _finding("b", "group_compare", "units", "region", 8.5)]
    merges = {"a": {"items": [{"measure": "revenue", "text": "revenue: N at 100"}, {"measure": "units", "text": "units: N at 5"}]}}
    res = _result(fs, ["a"], merges=merges)
    res["curation"]["absorbed"] = {"b": "a", "ch_b": "ch_a"}
    plan = dashplan.build_plan(_dash(dims=("region",), date=False), res)
    live = [t for t in plan["tiles"] + plan["more"] if t["id"] == "dim:region"][0]
    assert [i["text"] for i in live["items"]] == ["revenue: N at 100", "units: N at 5"]
    assert not [t for t in plan["tiles"] + plan["more"] if t["id"] in ("chart:a", "chart:b")]


def test_a_merged_line_without_a_figure_reads_as_the_findings_own_headline():
    from app import curate
    c = {"id": "x", "measure": "revenue", "dimension": "region", "tool": "outcome_compare", "title": "Region N leads on revenue"}
    assert curate._item(c)["text"] == "Region N leads on revenue"


# ------------------------------------------------------------------ Lens, export and Power BI follow the same selection
def _same_leader_df():
    import pandas as pd
    rng = np.random.default_rng(5)
    n = 900
    region = rng.choice(list("NSEW"), n)
    base = np.where(region == "N", 3.0, np.where(region == "S", 2.0, np.where(region == "E", 1.2, 1.0)))
    return pd.DataFrame({"date": pd.date_range("2023-01-01", periods=n, freq="D"), "region": region, "channel": rng.choice(["web", "store", "app"], n),
                         "revenue": (100 * base + rng.normal(0, 10, n)).round(2), "units": (5 * base + rng.normal(0, .5, n)).round(2),
                         "profit": (30 * base + rng.normal(0, 4, n)).round(2)})


def test_lens_can_show_a_more_item_on_request_and_it_joins_the_dashboard():
    from test_new_tools_and_views import run
    rid, res = run(_same_leader_df(), "Which region is best?")
    c = TestClient(app)
    before = c.get(f"/api/runs/{rid}/dashboard").json()["plan"]
    more = [t for t in before["more"] if t["kind"] == "finding"]
    assert more, "this data should leave at least one chart in the More list"
    pick = more[0]["finding_id"]
    assert not [t for t in before["tiles"] if t.get("finding_id") == pick]
    r = c.post(f"/api/runs/{rid}/view", json={"command": {"cmd": "show", "ids": [f"dashboard:chart:{pick}"]}})      # the same command Lens sends for "show me that chart"
    assert r.status_code == 200 and "Added to the Dashboard" in r.json()["message"]
    after = c.get(f"/api/runs/{rid}/dashboard").json()["plan"]
    assert pick in [t.get("finding_id") for t in after["tiles"]]
    assert pick not in [t.get("finding_id") for t in after["more"]]
    assert dashplan.covered(after["layout"]["tiles"]) == (dashplan.GRID_COLS * dashplan.GRID_ROWS, 0)
    c.post(f"/api/runs/{rid}/view", json={"command": {"cmd": "undo"}})
    assert pick not in [t.get("finding_id") for t in c.get(f"/api/runs/{rid}/dashboard").json()["plan"]["tiles"]]


def test_a_chart_the_selection_left_out_entirely_can_still_be_asked_for():
    fs = [_finding("a", "group_compare", "revenue", "region", 9.0), _finding("z", "scatter", "units", "", 0.1)]
    res = _result(fs, ["a"])                                        # "z" is neither kept nor in More
    assert "dashboard:chart:z" in [f"dashboard:chart:{f['id']}" for f in fs]
    plan = dashplan.build_plan(_dash(dims=(), date=False), res, ["dashboard:chart:z"])
    assert "chart:z" in [t["id"] for t in plan["tiles"]]
    assert dashplan.covered(plan["layout"]["tiles"]) == (dashplan.GRID_COLS * dashplan.GRID_ROWS, 0)


def test_print_report_and_power_bi_follow_the_selection():
    from pathlib import Path
    from app import powerbi
    from app.lens import exporter
    from app.runner import RUNS_DIR
    from test_new_tools_and_views import run
    rid, res = run(_same_leader_df(), "Which region is best?")
    cards = exporter.curated_cards(res)
    assert cards and len(cards) < len(res["findings"])
    html_ = exporter.report_html(res, Path(RUNS_DIR) / rid)
    for c in cards:
        if c.get("chart"):
            assert c["headline"].split(" (")[0][:20] in html_
    merged = [c for c in cards if c.get("merged")]
    assert merged and merged[0]["merged"]["items"][1]["text"] in html_ and html_.count(merged[0]["merged"]["items"][1]["text"]) == 1
    vis = powerbi.build_visuals(res["findings"], res["plan"], res["curation"])
    every = powerbi.build_visuals(res["findings"], res["plan"], None)
    assert len(vis) < len(every)
    chosen, left = powerbi.selected_findings(res["findings"], res["curation"])
    assert left > 0 and not ({f["id"] for f in chosen} & set(res["curation"]["absorbed"]))
    assert powerbi.selected_findings(res["findings"], {})[1] == 0                  # an older run: everything, as before


def test_a_merged_cards_measure_lines_use_display_names_even_for_a_run_saved_with_column_names():
    fs = [_finding("c", "group_compare", "net_received", "channel", 7.0)]
    merges = {"c": {"dimension": "channel", "leader": "Ethnic", "items": [{"id": "c", "measure": "net_received", "figure": 3447.0, "text": "net_received: Ethnic at 3,447"},
                                                                         {"id": "d", "measure": "discount_amount", "figure": 248.0, "text": "discount_amount: Ethnic at 248"}]}}
    res = _result(fs, ["c"], merges=merges)
    res["profile"] = {"columns": [{"name": "net_received"}, {"name": "discount_amount"}, {"name": "channel"}]}
    res["understanding"] = {"source": "llm", "columns": {"net_received": {"display_name": "Net Received"}, "discount_amount": {"display_name": "Discount Amount"}}}
    plan = dashplan.build_plan(_dash(dims=("region",)), res)
    tile = next(t for t in plan["tiles"] + plan["more"] if t.get("finding_id") == "c")
    assert [i["text"] for i in tile["items"]] == ["Net Received: Ethnic at 3,447", "Discount Amount: Ethnic at 248"]
    assert [i["measure"] for i in tile["items"]] == ["net_received", "discount_amount"]          # the ids stay


def test_curate_writes_display_names_into_the_merged_lines():
    from app import curate
    from tests.test_curate import _cmp, _state
    fs = [_cmp(1, "net_received", "area", "North", "South", score=9.0), _cmp(2, "discount_amount", "area", "North", "South", score=8.0)]
    st = _state(findings=fs, goal="anything interesting")
    st["profile"]["columns"] = [{"name": "net_received"}, {"name": "discount_amount"}]
    st["understanding"] = {"source": "llm", "columns": {"net_received": {"display_name": "Net Received"}, "discount_amount": {"display_name": "Discount Amount"}}}
    (info,) = curate.curate(st)["merges"].values()
    assert [i["text"].split(":")[0] for i in info["items"]] == ["Net Received", "Discount Amount"]
