"""Слой 5 и сценарии 11–12 из docs/07-testing-acceptance.md §2."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.guard import WatchState, watch_once
from adhelper.budget.domain.balance import BalanceState, check_balance
from adhelper.budget.domain.calendar import moscow_period, platform_date
from adhelper.budget.domain.limits import (
    BudgetPeriod,
    CampaignBudget,
    LimitChange,
    RejectionReason,
    check_limit_change,
)
from adhelper.budget.domain.watch import BurnRate, SpendSnapshot, WatchAction
from tests.budget.fake_platform import FakePlatform

RULES = load_rules("budget-1")
HARD = 1_000_000_000


def campaign(limit: int = 300_000_000) -> CampaignBudget:
    return CampaignBudget("a", limit, 0, True, None)


def test_platform_date_uses_moscow_time() -> None:
    assert platform_date(datetime(2026, 10, 31, 20, 59, tzinfo=UTC)) == date(2026, 10, 31)
    assert platform_date(datetime(2026, 10, 31, 21, 0, tzinfo=UTC)) == date(2026, 11, 1)


def test_period_bounds_are_moscow_midnights_in_utc() -> None:
    starts_at, ends_at = moscow_period(date(2026, 11, 1), days=30)

    assert starts_at == datetime(2026, 10, 31, 21, 0, tzinfo=UTC)
    assert ends_at == datetime(2026, 11, 30, 21, 0, tzinfo=UTC)


@given(st.dates(date(2024, 1, 1), date(2035, 1, 1)), st.integers(1, 366))
def test_period_length_is_exact_days(start: date, days: int) -> None:
    starts_at, ends_at = moscow_period(start, days)

    assert ends_at - starts_at == timedelta(days=days)
    assert starts_at.tzinfo is UTC


def test_scenario_11_increase_is_rejected_after_period_end_across_month() -> None:
    """В UTC ещё 30 ноября, но по Москве период, закончившийся в полночь, уже завершён."""
    starts_at, ends_at = moscow_period(date(2026, 11, 1), days=30)
    period = BudgetPeriod(HARD, 1000, starts_at=starts_at, ends_at=ends_at)
    change = LimitChange("a", 330_000_000)

    inside = datetime(2026, 11, 30, 20, 59, tzinfo=UTC)
    after = datetime(2026, 11, 30, 21, 0, tzinfo=UTC)

    assert check_limit_change(period, [campaign()], change, 0, inside, RULES).allowed
    late = check_limit_change(period, [campaign()], change, 0, after, RULES)
    assert late.reason is RejectionReason.PERIOD_ENDED


def test_scenario_11_decrease_stays_allowed_after_period_end() -> None:
    starts_at, ends_at = moscow_period(date(2026, 11, 1), days=30)
    period = BudgetPeriod(HARD, 1000, starts_at=starts_at, ends_at=ends_at)

    decision = check_limit_change(
        period, [campaign()], LimitChange("a", 200_000_000), 0, ends_at, RULES
    )

    assert decision.allowed


def test_balance_matching_spend_is_ok() -> None:
    assert check_balance(1_000_000, 500_000, 500_000, HARD, RULES) is BalanceState.OK


def test_balance_dropping_more_than_reported_is_mismatch() -> None:
    tolerance = HARD * RULES.balance_tolerance_bp // 10_000
    opening = 2 * HARD

    edge = check_balance(opening, opening - 100 - tolerance, 100, HARD, RULES)
    over = check_balance(opening, opening - 101 - tolerance, 100, HARD, RULES)

    assert edge is BalanceState.OK
    assert over is BalanceState.MISMATCH


def test_top_up_is_not_a_mismatch() -> None:
    """Пополнение нам не видно: баланс вырос, это не может быть скрытым расходом."""
    assert check_balance(1_000, 900_000_000, 100, HARD, RULES) is BalanceState.OK


def test_zero_balance_is_reported_first() -> None:
    assert check_balance(1_000_000, 0, 1_000_000, HARD, RULES) is BalanceState.ZERO


@given(
    st.integers(1, 10**12), st.integers(1, 10**12), st.integers(0, 10**12), st.integers(1, 10**13)
)
def test_more_reported_spend_never_creates_mismatch(
    opening: int, current: int, reported: int, hard: int
) -> None:
    first = check_balance(opening, current, reported, hard, RULES)
    second = check_balance(opening, current, reported + 1, hard, RULES)

    if first is BalanceState.OK:
        assert second is BalanceState.OK


NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
PERIOD = BudgetPeriod(hard_limit=HARD, reserve_bp=1000)


async def test_layer_5_hidden_spend_stops_campaigns() -> None:
    """Статистика показывает мало, а деньги со счёта ушли: верим счёту и останавливаемся."""
    platform = FakePlatform([campaign()], NOW, balance=900_000_000)
    platform.run(minutes=10, per_minute=1_000_000)
    platform.balance -= 200_000_000

    result = await watch_once(
        platform,
        "acc",
        PERIOD,
        WatchState.initial(),
        platform.now,
        RULES,
        opening_balance=900_000_000,
    )

    assert result.balance_state is BalanceState.MISMATCH
    assert result.stopped
    assert result.verdict is not None
    assert result.verdict.action is not WatchAction.STOP


async def test_scenario_12_zero_balance_alerts_without_touching_campaigns() -> None:
    platform = FakePlatform([campaign()], NOW, balance=5_000_000)
    platform.run(minutes=60, per_minute=1_000_000)
    assert platform.total_spent == 5_000_000

    result = await watch_once(
        platform,
        "acc",
        PERIOD,
        WatchState.initial(),
        platform.now,
        RULES,
        opening_balance=5_000_000,
    )

    assert result.balance_state is BalanceState.ZERO
    assert not result.stopped
    assert platform.campaigns["a"].active
    assert platform.set_limit_calls == []


async def test_balance_unavailable_does_not_block_spend_check() -> None:
    platform = FakePlatform([campaign(2_000_000_000)], NOW, enforces_limits=False)
    platform.run(minutes=1, per_minute=980_000_000)
    platform.faults_for_balance = True

    result = await watch_once(
        platform, "acc", PERIOD, WatchState.initial(), platform.now, RULES, opening_balance=1
    )

    assert result.balance_state is None
    assert result.stopped


def test_rules_field_exists_in_file() -> None:
    assert replace(RULES).balance_tolerance_bp == 200


async def test_older_statistics_do_not_replace_newer_snapshot() -> None:
    start = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    platform = FakePlatform([campaign()], start)
    platform.run(60, 1_000_000)
    seen = SpendSnapshot(30_000_000, start + timedelta(minutes=30))
    platform.stats_frozen_at = start

    result = await watch_once(
        platform,
        "acc",
        BudgetPeriod(HARD, 1000),
        WatchState(seen, BurnRate.zero(), False),
        platform.now,
        RULES,
    )

    assert result.state.snapshot == seen
