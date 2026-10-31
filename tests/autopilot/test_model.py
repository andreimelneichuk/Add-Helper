"""Оценка эффективности: docs/04-autopilot.md §4."""

from datetime import UTC, date, datetime

from hypothesis import given
from hypothesis import strategies as st

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.domain.model import (
    AdStats,
    DailyStat,
    Verdict,
    classify,
    mature_totals,
    posterior,
)

RULES = load_rules("autopilot-1")
TARGET = 400_000_000
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def ad(spend: int, leads: int, age_days: int = 30) -> AdStats:
    return AdStats("ad:1", "c1", "g1", spend, leads, age_days, active=True)


@given(st.integers(0, 10**11), st.integers(0, 200), st.integers(1, 10**8))
def test_probabilities_stay_in_bounds(spend: int, leads: int, extra: int) -> None:
    result = posterior(spend, leads, TARGET, RULES)
    assert 0 <= result.p_good_bp <= 10000
    assert 0 <= result.p_bad_bp <= 10000
    # Цена не может быть одновременно ниже цели и выше 1,3 цели.
    assert result.p_good_bp + result.p_bad_bp <= 10000


@given(st.integers(0, 10**11), st.integers(0, 100), st.integers(1, 10**10))
def test_more_spend_without_leads_never_looks_better(spend: int, leads: int, extra: int) -> None:
    before = posterior(spend, leads, TARGET, RULES)
    after = posterior(spend + extra, leads, TARGET, RULES)
    assert after.p_bad_bp >= before.p_bad_bp
    assert after.p_good_bp <= before.p_good_bp


@given(st.integers(0, 10**11), st.integers(0, 100))
def test_extra_lead_never_looks_worse(spend: int, leads: int) -> None:
    before = posterior(spend, leads, TARGET, RULES)
    after = posterior(spend, leads + 1, TARGET, RULES)
    assert after.p_good_bp >= before.p_good_bp
    assert after.p_bad_bp <= before.p_bad_bp


def test_large_lead_counts_do_not_overflow() -> None:
    result = posterior(4_000_000 * TARGET, 10_000, TARGET, RULES)
    assert 0 <= result.p_good_bp <= 10000


def test_clearly_bad_ad_is_inefficient() -> None:
    assert classify(ad(TARGET * 5, 0), TARGET, RULES).verdict is Verdict.INEFFICIENT


def test_bad_probability_alone_is_not_enough_below_spend_floor() -> None:
    """Даже при высокой P_bad тратим минимум 2 цели, прежде чем отключать."""
    evaluation = classify(ad(TARGET * 2 - 1, 0), TARGET, RULES)
    assert evaluation.verdict is not Verdict.INEFFICIENT


def test_clearly_good_ad_is_efficient() -> None:
    assert classify(ad(TARGET * 10, 20), TARGET, RULES).verdict is Verdict.EFFICIENT


def test_spend_below_target_is_low_data() -> None:
    assert classify(ad(TARGET - 1, 3), TARGET, RULES).verdict is Verdict.LOW_DATA


def test_learning_period_is_low_data_even_with_big_spend() -> None:
    fresh = ad(TARGET * 10, 0, age_days=3)
    young_and_cheap = ad(TARGET, 1, age_days=8)
    assert classify(fresh, TARGET, RULES).verdict is Verdict.INEFFICIENT
    assert classify(ad(TARGET, 0, age_days=3), TARGET, RULES).verdict is Verdict.LOW_DATA
    assert classify(young_and_cheap, TARGET, RULES).verdict is not Verdict.INEFFICIENT


def test_middle_ground_is_borderline() -> None:
    assert classify(ad(TARGET * 3, 3), TARGET, RULES).verdict is Verdict.BORDERLINE


def test_totals_exclude_last_48_hours_and_untrusted_days() -> None:
    rows = [
        DailyStat(date(2026, 11, 16), 100, 1, 10, 10),
        DailyStat(date(2026, 11, 17), 200, 1, 10, 10, trusted=False),
        DailyStat(date(2026, 11, 18), 400, 1, 10, 10),
        DailyStat(date(2026, 11, 19), 800, 1, 10, 10),
        DailyStat(date(2026, 11, 20), 1600, 1, 10, 10),
    ]
    assert mature_totals(rows, NOW, RULES).spend == 500
    assert mature_totals(rows, NOW, RULES).leads == 2


def test_maturity_boundary_uses_moscow_date() -> None:
    late_utc = datetime(2026, 11, 20, 21, 30, tzinfo=UTC)  # В Москве уже 21 ноября.
    rows = [DailyStat(date(2026, 11, 19), 100, 1, 1, 1), DailyStat(date(2026, 11, 18), 7, 0, 1, 1)]
    assert mature_totals(rows, late_utc, RULES).spend == 107
