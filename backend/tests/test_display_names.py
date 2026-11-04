"""Fix E: every sentence, title and table a reader sees shows a column's display name; ids, parameters and file paths keep the internal names. Fake AI only."""
import json
import os
import re

os.environ["LLM_PROVIDER"] = "none"

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from app import tiering, tools, understanding as und  # noqa: E402
from tests.fixtures_bakery import bakery  # noqa: E402


def state_for(names, shown):
    rec = {"source": "llm", "columns": {n: {"role": "measure", "display_name": shown.get(n, "")} for n in names}, "goal_columns": {}}
    return {"profile": {"columns": [{"name": n} for n in names]}, "plan": {"derived": [{"name": "amount_to_unit_price_ratio", "a": "amount", "b": "unitprice"}]}, "understanding": rec}


def test_a_joined_name_gets_the_display_name_the_record_gives_it():
    st = state_for(["costamount", "unitprice", "amount", "customer_type"], {"costamount": "Cost amount", "unitprice": "Unit price", "amount": "Amount"})
    names = und.display_names(st)
    assert names["costamount"] == "Cost amount" and names["unitprice"] == "Unit price"
    assert "amount" not in names                                    # only its capital would change: left alone in a sentence
    assert names["customer_type"] == "Customer type" and names["amount_to_unit_price_ratio"] == "Amount to unit price ratio"
    assert und.readable("By total costamount, unitprice is highest; amount_to_unit_price_ratio is flat", st) == "By total Cost amount, Unit price is highest; Amount to unit price ratio is flat"


def test_markdown_keeps_file_paths_and_changes_the_words():
    st = state_for(["unit_cost", "item"], {})
    md = "![unit_cost by item](charts/cmp_unit_cost_item.png)\nThe unit_cost is `unit_cost`."
    out = und.readable_md(md, st)
    assert "(charts/cmp_unit_cost_item.png)" in out and out.startswith("![Unit cost by item]") and out.endswith("The Unit cost is `Unit cost`.")


def test_the_view_shows_display_names_in_its_text_and_keeps_ids_and_parameters():
    st = state_for(["unit_price", "item_code"], {})
    view = {"answers": [{"question": "q", "answer": "unit_price by item_code", "ids": ["r1"]}], "headlines": [{"id": "r1", "text": "unit_price is high"}],
            "rankings": [{"id": "r1", "group": "item_code", "measure": "unit_price", "top": "x", "lead": "Top item_code by unit_price", "text": "unit_price per item_code",
                          "groups": [{"group": "x", "line": "item_code x"}]}],
            "actions": [{"text": "Raise unit_price", "evidence": ["f1"]}],
            "evidence": [{"id": "f1", "tool": "group_compare", "title": "unit_price by item_code", "headline": "h unit_price", "checks": ["median unit_price"], "stats": {"best": "unit_price"},
                          "tables": [{"title": "unit_price by item_code (table)", "columns": ["Group"], "rows": [["a", 1]]}], "also": [{"headline": "item_code again"}]}],
            "appendix": [], "checked": [{"id": "f4", "target": "unit_price", "group": "item_code", "line": "No pattern in unit_price", "check": ""}],
            "numbers": [{"label": "Total unit_price", "value": "1", "note": "unit_price summed"}], "profile": {"distributions": [{"id": "f9", "line": "unit_price spread"}], "tables": [{"id": "f12", "title": "unit_price table", "rows": []}]}}
    before = json.dumps(view, sort_keys=True)
    out = tiering.pretty_view(view, st)
    assert json.dumps(view, sort_keys=True) == before                           # the facts the view was built from are not changed
    assert out["answers"][0]["answer"] == "Unit price by Item code" and out["headlines"][0]["text"] == "Unit price is high"
    r = out["rankings"][0]
    assert (r["group"], r["measure"], r["id"]) == ("item_code", "unit_price", "r1") and r["lead"] == "Top Item code by Unit price" and r["groups"][0]["line"] == "Item code x"
    e = out["evidence"][0]
    assert e["title"] == "Unit price by Item code" and e["checks"] == ["median Unit price"] and e["stats"] == {"best": "unit_price"}
    assert e["tables"][0]["title"] == "Unit price by Item code (table)" and e["also"][0]["headline"] == "Item code again"
    assert out["checked"][0]["line"] == "No pattern in Unit price" and out["checked"][0]["target"] == "unit_price"
    assert out["numbers"][0]["label"] == "Total unit_price" and out["numbers"][0]["note"] == "Unit price summed"          # labels are matched by other code; tiles show the display name
    assert out["profile"]["distributions"][0]["line"] == "Unit price spread" and out["actions"][0]["text"] == "Raise Unit price"


def test_chart_text_shows_display_names_but_the_file_keeps_its_name(tmp_path):
    fig, ax = plt.subplots()
    ax.plot([1, 2], [1, 2], label="trend of unit_cost")
    ax.set_title("unit_cost by item_code")
    ax.set_ylabel("Sum unit_cost per month")
    ax.legend()
    path = tools._save(fig, {"charts_dir": tmp_path, "display_names": {"unit_cost": "Unit cost", "item_code": "Item code"}}, "cmp_unit_cost_item_code")
    assert path == "charts/cmp_unit_cost_item_code.png" and (tmp_path / "cmp_unit_cost_item_code.png").exists()
    assert ax.get_title(loc="left") == "Unit cost by Item code" and ax.get_ylabel() == "Sum Unit cost per month" and ax.get_legend().get_texts()[0].get_text() == "trend of Unit cost"


def test_the_whole_report_of_a_bakery_run_names_no_column_by_its_internal_name(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("GLOSSARY_PATH", str(tmp_path / "g.json"))
    from app import runner
    df = bakery()
    rid = runner.create_run("", [("bakery.csv", df.to_csv(index=False).encode())])
    runner._execute(rid)
    wd = runner.run_dir(rid)
    if (runner.read_status(rid) or {}).get("status") == "needs_input":
        (wd / "answers.json").write_text(json.dumps({"answers": {}, "skip": True, "notes": ""}))
        runner._execute(rid)
    res = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    internal = [c for c in df.columns if "_" in c] + ["amount_to_unit_price_ratio"]
    rx = re.compile("(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, internal)) + ")(?![A-Za-z0-9_])")
    md = res["report_md"]
    assert not rx.findall(md), rx.findall(md)[:5]
    assert not rx.findall(res["report_view"]["page"]["behind_md"])
    assert not rx.findall(json.dumps([c["title"] for c in res["report_view"]["page"]["charts"]]))
    assert all((wd / p).exists() for p in re.findall(r"\]\((charts/[^)]+)\)", md))          # the image links still point at real files
    assert {r["group"] for r in res["report_view"]["rankings"]} & {"item_code", "item"}        # parameters keep the internal names
