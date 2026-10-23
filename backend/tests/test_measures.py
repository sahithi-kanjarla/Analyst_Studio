"""measure_kind / has_sum_word / is_money: same answers as the old per-file word lists; an override wins."""
import pytest

from app import tools
from app.measures import has_sum_word, is_money, measure_kind


@pytest.mark.parametrize("name,kind", [
    ("amount", "additive"), ("order_total", "additive"), ("revenue", "additive"), ("units_sold", "additive"),
    ("profit_to_amount_ratio", "rate"), ("unit_price", "rate"), ("discount_pct", "rate"), ("total_pct", "rate"), ("age", "rate"),
    ("customer_name", "other"), ("rating", "other"),
])
def test_measure_kind_matches_legacy_rule(name, kind):
    assert measure_kind(name) == kind
    assert tools.additive(name) == (kind == "additive")


def test_has_sum_word_is_the_looser_legacy_test():
    assert has_sum_word("total_pct") and not has_sum_word("rating")


def test_is_money_wide_and_narrow_lists():
    assert is_money("payment") and is_money("gmv") and is_money("revenue")
    assert not is_money("payment", narrow=True) and is_money("revenue", narrow=True)
    assert not is_money("quantity")


def test_override_wins():
    assert measure_kind("unit_price", overrides={"unit_price": "additive"}) == "additive"
    assert measure_kind("revenue", overrides={"revenue": "rate"}) == "rate"
    assert measure_kind("rating", overrides={"rating": "additive"}) == "additive"
    assert measure_kind("revenue", overrides={"other_col": "rate"}) == "additive"  # no entry for this column
    assert has_sum_word("rating", overrides={"rating": "additive"}) and not has_sum_word("revenue", overrides={"revenue": "rate"})
