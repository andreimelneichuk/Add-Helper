"""Оценка исхода действия автопилота: шум не даёт «хуже», настоящее ухудшение даёт."""

from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.autopilot.domain.outcomes import (
    OutcomeRules,
    OutcomeVerdict,
    Period,
    evaluate_outcome,
)

RULES = OutcomeRules(
    version="outcomes-1",
    window_days=7,
    min_clicks=30,
    min_leads=5,
    worse_threshold_bp=2_000,
    better_threshold_bp=2_000,
    noise_sigmas_x10=20,
    zero_leads_spend_multiple=3,
)
RUB = 1_000_000


def period(clicks: int, spend_rub: int, leads: int) -> Period:
    return Period(clicks=clicks, spend=spend_rub * RUB, leads=leads)


def test_noise_within_poisson_spread_is_not_worse() -> None:
    # 10 против 8 обращений при той же цене клика: разница в пределах шума.
    before = period(200, 6_000, 10)
    after = period(200, 6_000, 8)
    assert evaluate_outcome(before, after, RULES).verdict is OutcomeVerdict.SAME


def test_real_worsening_is_worse() -> None:
    before = period(400, 6_000, 40)
    after = period(400, 6_000, 20)
    result = evaluate_outcome(before, after, RULES)
    assert result.verdict is OutcomeVerdict.WORSE
    assert result.cpl_change_bp == 10_000  # цена обращения выросла вдвое


def test_real_improvement_is_better() -> None:
    result = evaluate_outcome(period(400, 6_000, 40), period(400, 6_000, 100), RULES)
    assert result.verdict is OutcomeVerdict.BETTER


def test_too_few_clicks_or_leads_is_insufficient() -> None:
    assert (
        evaluate_outcome(period(400, 6_000, 40), period(10, 100, 1), RULES).verdict
        is OutcomeVerdict.INSUFFICIENT
    )
    assert (
        evaluate_outcome(period(400, 6_000, 3), period(400, 6_000, 1), RULES).verdict
        is OutcomeVerdict.INSUFFICIENT
    )


def test_zero_leads_after_with_big_spend_is_worse() -> None:
    # До: 40 обращений за 6000 ₽ — 150 ₽ за обращение. После: 600 ₽ без единого — 4 цены.
    result = evaluate_outcome(period(400, 6_000, 40), period(100, 600, 0), RULES)
    assert result.verdict is OutcomeVerdict.WORSE


def test_zero_leads_after_with_small_spend_is_insufficient() -> None:
    result = evaluate_outcome(period(400, 6_000, 40), period(40, 200, 0), RULES)
    assert result.verdict is OutcomeVerdict.INSUFFICIENT


@given(
    st.integers(0, 10**7),
    st.integers(0, 10**13),
    st.integers(0, 10**6),
    st.integers(0, 10**7),
    st.integers(0, 10**13),
    st.integers(0, 10**6),
)
def test_never_raises_and_stays_integer(
    c1: int, s1: int, l1: int, c2: int, s2: int, l2: int
) -> None:
    result = evaluate_outcome(Period(c1, s1, l1), Period(c2, s2, l2), RULES)
    assert isinstance(result.cpl_change_bp, int) or result.cpl_change_bp is None
    assert isinstance(result.noise_bp, int)


@given(st.integers(30, 10_000), st.integers(5, 5_000))
def test_identical_periods_are_never_worse(clicks: int, leads: int) -> None:
    period_ = Period(clicks=clicks, spend=clicks * 30 * RUB, leads=leads)
    assert evaluate_outcome(period_, period_, RULES).verdict is OutcomeVerdict.SAME


@given(st.integers(50, 2_000), st.integers(10, 400), st.integers(1, 5_000))
def test_more_leads_for_same_spend_never_worse(clicks: int, leads: int, extra: int) -> None:
    before = Period(clicks, clicks * 30 * RUB, leads)
    after = Period(clicks, clicks * 30 * RUB, leads + extra)
    assert evaluate_outcome(before, after, RULES).verdict is not OutcomeVerdict.WORSE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("window_days", 0),
        ("min_clicks", -1),
        ("min_leads", 0),
        ("worse_threshold_bp", 0),
        ("better_threshold_bp", 0),
        ("noise_sigmas_x10", -1),
        ("zero_leads_spend_multiple", 0),
        ("maturity_days", -1),
        ("max_rollbacks", 0),
        ("rollback_window_days", 0),
        ("anomaly_window", 0),
        ("anomaly_worse_count", 0),
        ("anomaly_worse_count", 6),
        ("max_wait_days", -1),
        ("register_lookback_days", 8),
    ],
)
def test_nonsense_rules_are_refused(field: str, value: int) -> None:
    changes: dict[str, Any] = {field: value}
    with pytest.raises(ValueError):
        replace(RULES, **changes)


def test_shipped_rules_pass_their_own_validation() -> None:
    from adhelper.autopilot.adapters.rules_file import load_outcome_rules

    assert load_outcome_rules("outcomes-1").min_leads >= 1


def test_zero_leads_explanation_states_the_numbers() -> None:
    from adhelper.autopilot.domain.explain import explain_rollback_no_leads

    text = explain_rollback_no_leads(
        {"after_spend_micros": 8_400 * RUB, "before_cpl_micros": 200 * RUB}
    )
    assert "обращений 0" in text and "8 400 ₽" in text and "200 ₽" in text
    assert "выросла" not in text


def test_window_longer_than_a_week_is_rejected() -> None:
    # Окно «до» сдвинуто на неделю: при большем окне оно пересеклось бы с окном «после».
    with pytest.raises(ValueError, match="window_days"):
        replace(RULES, window_days=8, register_lookback_days=30)
