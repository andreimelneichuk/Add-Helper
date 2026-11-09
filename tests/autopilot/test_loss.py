"""Математика autopilot-3: Beta-Binomial, интервалы, ожидаемая потеря (docs/16, §11)."""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from adhelper.autopilot.domain.loss import (
    belief,
    compare_rates,
    cost_interval_micros,
    expected_loss_micros,
    rate_interval,
    rate_mean,
)

PRIOR_CLICKS = 20
TARGET = 300_000_000  # 300 ₽


def example(clicks_a: int, leads_a: int, clicks_b: int, leads_b: int):  # type: ignore[no-untyped-def]
    group_clicks, group_leads = clicks_a + clicks_b, leads_a + leads_b
    a = belief(clicks_a, leads_a, group_clicks, group_leads, PRIOR_CLICKS)
    b = belief(clicks_b, leads_b, group_clicks, group_leads, PRIOR_CLICKS)
    return a, b


def test_example_a20_of_400_against_b10_of_400_b_is_worse_with_high_probability() -> None:
    a, b = example(400, 20, 400, 10)
    result = compare_rates(b, a)
    # §11: «около 96%». С поправкой на группу слегка ниже, но не меньше 94%.
    assert 9400 <= result.p_worse_bp <= 9700


def test_example_expected_loss_is_about_thousands_of_rubles_per_week() -> None:
    a, b = example(400, 20, 400, 10)
    gap = compare_rates(b, a).expected_gap
    # 400 кликов в неделю; §11: порядка 10 обращений, около 3000 ₽ при цене 300 ₽.
    loss = expected_loss_micros(400, gap, TARGET)
    assert 2_000_000_000 <= loss <= 3_300_000_000


def test_posterior_agrees_with_independent_sampling() -> None:
    """Сетка против розыгрыша: другая математика, тот же ответ в пределах шума."""
    a, b = example(400, 20, 400, 10)
    rng = random.Random(7)
    draws = 60_000
    worse = 0
    gap = 0.0
    for _ in range(draws):
        ra = rng.betavariate(a.alpha, a.beta)
        rb = rng.betavariate(b.alpha, b.beta)
        worse += rb < ra
        gap += max(ra - rb, 0.0)
    result = compare_rates(b, a)
    assert abs(result.p_worse_bp - worse / draws * 10000) < 90
    assert abs(result.expected_gap - gap / draws) < 0.0008


def test_equal_ads_are_a_coin_flip_and_have_small_loss() -> None:
    a, b = example(400, 20, 400, 20)
    result = compare_rates(b, a)
    assert 4800 <= result.p_worse_bp <= 5200


def test_interval_contains_mean_and_is_inside_unit_range() -> None:
    value = belief(100, 3, 500, 23, PRIOR_CLICKS)
    low, high = rate_interval(value, 500)
    assert 0 < low < rate_mean(value) < high < 1


def test_few_clicks_give_much_wider_interval_than_many() -> None:
    few = belief(100, 3, 500, 23, PRIOR_CLICKS)
    many = belief(400, 10, 500, 23, PRIOR_CLICKS)
    few_low, few_high = rate_interval(few, 500)
    many_low, many_high = rate_interval(many, 500)
    assert few_high / few_low > many_high / many_low


@settings(max_examples=60, deadline=None)
@given(
    clicks=st.integers(min_value=40, max_value=4000),
    share=st.floats(min_value=0.005, max_value=0.4),
)
def test_more_data_never_widens_the_interval(clicks: int, share: float) -> None:
    leads = round(clicks * share)
    prior = (2000, 100)
    one = belief(clicks, leads, *prior, PRIOR_CLICKS)
    twice = belief(clicks * 2, leads * 2, *prior, PRIOR_CLICKS)
    one_low, one_high = rate_interval(one, 500)
    two_low, two_high = rate_interval(twice, 500)
    assert (two_high - two_low) <= (one_high - one_low) + 1e-9


@settings(max_examples=40, deadline=None)
@given(
    clicks_b=st.integers(min_value=30, max_value=3000),
    leads_b=st.integers(min_value=0, max_value=150),
    clicks_a=st.integers(min_value=30, max_value=3000),
    leads_a=st.integers(min_value=0, max_value=150),
)
def test_same_input_same_answer_and_probabilities_are_complementary(
    clicks_b: int, leads_b: int, clicks_a: int, leads_a: int
) -> None:
    leads_a, leads_b = min(leads_a, clicks_a), min(leads_b, clicks_b)
    a, b = example(clicks_a, leads_a, clicks_b, leads_b)
    first, again = compare_rates(b, a), compare_rates(b, a)
    reverse = compare_rates(a, b)
    assert first == again
    assert 0 <= first.p_worse_bp <= 9999
    # Вероятности «B хуже» и «A хуже» складываются в единицу с точностью до округления вниз.
    assert 9990 <= first.p_worse_bp + reverse.p_worse_bp <= 10000
    assert first.expected_gap >= 0


def test_cost_interval_is_integer_micros_and_ordered() -> None:
    value = belief(400, 10, 800, 30, PRIOR_CLICKS)
    low_rate, high_rate = rate_interval(value, 500)
    low, high = cost_interval_micros(
        spend=2_000_000_000, clicks=400, low_rate=low_rate, high_rate=high_rate
    )
    assert isinstance(low, int) and isinstance(high, int)
    assert 0 < low < high


def test_expected_loss_rounds_down_to_whole_micros() -> None:
    assert expected_loss_micros(400, 0.0249999, TARGET) == int(400 * 0.0249999 * TARGET)
    assert expected_loss_micros(0, 0.5, TARGET) == 0


def test_zero_and_huge_counts_never_raise_and_say_little() -> None:
    from adhelper.autopilot.domain.loss import (
        belief,
        compare_rates,
        cost_interval_micros,
        rate_interval,
    )

    empty = belief(0, 0, 0, 0, 0)
    huge = belief(10**15, 3 * 10**13, 10**15, 3 * 10**13, 20)
    for value in (empty, huge):
        low, high = rate_interval(value, 500)
        assert 0.0 <= low <= high <= 1.0
    assert 0 <= compare_rates(empty, huge).p_worse_bp <= 9999
    assert compare_rates(huge, huge).expected_gap >= 0
    assert cost_interval_micros(0, 0, 0.0, 0.0) == (0, 0)
