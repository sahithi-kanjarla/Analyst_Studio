"""A percent change in AI text must name the measure (and the period) of the fact it cites, and must not disagree with the growth table for the same group, measure and periods."""
from app import cited

GROWTH = {"id": "r24", "kind": "growth", "group": "tier", "measure": "amount", "periods": ["2023", "2024"], "top": "Gold", "bottom": "Bronze",
          "groups": [{"group": "Gold", "pct": 0.41, "line": "tier Gold +41.0% (from 7,246,949 to 10,220,716)"}, {"group": "Silver", "pct": -0.09, "line": "tier Silver -9.0%"}],
          "text": "Growth in total amount by tier, 2023 vs 2024: tier Gold +41.0%; tier Silver -9.0%"}
TREND = {"id": "f6", "params": {"target": "amount", "date": "order_date", "group": "tier"}, "title": "amount over time by tier", "stats": {"kind": "segment_trend", "freq": "MS"},
         "text": "Change in amount over the period by tier (monthly, linear trend): Gold +88%; Silver +10%; Bronze -16%"}


def facts():
    return {"r24": cited.make_fact(GROWTH["text"], GROWTH["text"] + " tier Gold Silver", cited.scope_of(GROWTH)),
            "f6": cited.make_fact(TREND["text"], TREND["text"] + " tier Gold", cited.scope_of(TREND))}


def test_figure_of_another_measure_without_naming_it_is_dropped():
    _, why = cited.verify("Address the strong growth in Gold tier revenue (+88%) [f6].", facts())
    assert why and "measure" in why


def test_figure_with_measure_and_period_named_is_kept():
    assert cited.verify("The linear trend in amount shows Gold tier up +88% [f6].", facts())[1] is None
    assert cited.verify("Gold tier amount grew +41% from 2023 to 2024 [r24].", facts())[1] is None


def test_growth_figure_without_its_period_is_dropped():
    _, why = cited.verify("Gold tier amount grew +41% [r24].", facts())
    assert why and "period" in why


def test_conflicting_figure_for_the_same_measure_and_period_is_dropped():
    computed = cited.claims([GROWTH])
    clean, why = cited.verify("Gold tier amount grew +88% from 2023 to 2024 [r24].", {"r24": cited.make_fact(GROWTH["text"] + " +88%", "tier Gold", cited.scope_of(GROWTH))})
    assert why is None
    wrong = cited.contradicts(clean, computed)
    assert wrong and wrong["id"] == "r24" and "+41.0%" in wrong["text"]
    assert cited.contradicts("Gold tier amount grew +41% from 2023 to 2024.", computed) is None
    assert cited.contradicts("The linear trend in amount shows Gold tier up +88%.", computed) is None
