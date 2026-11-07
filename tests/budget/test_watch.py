from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.watch import (
    BurnRate,
    SpendSnapshot,
    WatchAction,
    corrected_spend,
    evaluate_watch,
)

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RULES = load_rules("budget-1")
PERIOD = BudgetPeriod(hard_limit=1_000_000, reserve_bp=1000)
ZERO = BurnRate.zero()


def fresh(reported: int) -> SpendSnapshot:
    return SpendSnapshot(reported=reported, data_as_of=NOW)


def test_below_alert_threshold_keeps_normal_interval() -> None:
    verdict = evaluate_watch(PERIOD, fresh(899_999), ZERO, NOW, RULES)

    assert verdict.action is WatchAction.OK
    assert verdict.next_check_seconds == RULES.check_interval_seconds
    assert verdict.rules_version == "budget-1"


def test_alert_threshold_shortens_interval() -> None:
    verdict = evaluate_watch(PERIOD, fresh(900_000), ZERO, NOW, RULES)

    assert verdict.action is WatchAction.ALERT
    assert verdict.next_check_seconds == RULES.alert_check_interval_seconds


def test_stop_threshold_stops() -> None:
    assert evaluate_watch(PERIOD, fresh(969_999), ZERO, NOW, RULES).action is WatchAction.ALERT
    assert evaluate_watch(PERIOD, fresh(970_000), ZERO, NOW, RULES).action is WatchAction.STOP


def test_data_age_and_burn_raise_projection() -> None:
    burn = BurnRate(amount=110, seconds=1)
    stale = SpendSnapshot(reported=500_000, data_as_of=NOW - timedelta(minutes=60))

    verdict = evaluate_watch(PERIOD, stale, burn, NOW, RULES)

    horizon = 3600 + RULES.check_interval_seconds + RULES.stop_latency_seconds
    assert verdict.projected == 500_000 + 110 * horizon
    assert verdict.action is WatchAction.STOP


def test_projection_rounds_up() -> None:
    assert BurnRate(amount=1, seconds=3).over(1) == 1


def test_burn_between_snapshots() -> None:
    older = SpendSnapshot(reported=1000, data_as_of=NOW - timedelta(seconds=100))
    newer = SpendSnapshot(reported=1500, data_as_of=NOW)

    assert BurnRate.between(older, newer, ZERO).over(10) == 50


def test_no_new_data_keeps_previous_burn() -> None:
    """Статистика зависла: нулевая скорость спрятала бы продолжающийся расход."""
    known = BurnRate(amount=7, seconds=1)

    assert BurnRate.between(fresh(1000), fresh(1000), known) == known


def test_corrected_spend_adds_burn_over_data_age() -> None:
    stale = SpendSnapshot(reported=1000, data_as_of=NOW - timedelta(seconds=10))

    assert corrected_spend(stale, BurnRate(amount=5, seconds=1), NOW) == 1050


def test_future_data_timestamp_does_not_reduce_spend() -> None:
    skewed = SpendSnapshot(reported=1000, data_as_of=NOW + timedelta(seconds=10))

    assert corrected_spend(skewed, BurnRate(amount=5, seconds=1), NOW) == 1000


ACTION_ORDER = [WatchAction.OK, WatchAction.ALERT, WatchAction.STOP]


@given(
    st.integers(0, 10**12),
    st.integers(0, 10**12),
    st.integers(0, 10**12),
    st.integers(0, 10**6),
    st.integers(1, 10**5),
    st.integers(0, 86_400),
)
def test_more_spend_never_lowers_severity(
    hard: int, low: int, extra: int, amount: int, seconds: int, age: int
) -> None:
    period = BudgetPeriod(hard_limit=hard, reserve_bp=1000)
    burn = BurnRate(amount=amount, seconds=seconds)
    moment = NOW - timedelta(seconds=age)

    first = evaluate_watch(period, SpendSnapshot(low, moment), burn, NOW, RULES)
    second = evaluate_watch(period, SpendSnapshot(low + extra, moment), burn, NOW, RULES)

    assert ACTION_ORDER.index(second.action) >= ACTION_ORDER.index(first.action)


@given(st.integers(1, 10**12), st.integers(0, 10**12))
def test_spend_at_or_over_stop_share_always_stops(hard: int, extra: int) -> None:
    period = BudgetPeriod(hard_limit=hard, reserve_bp=1000)
    spent = hard * RULES.stop_threshold_bp // 10_000 + 1 + extra

    verdict = evaluate_watch(period, SpendSnapshot(spent, NOW), ZERO, NOW, RULES)

    assert verdict.action is WatchAction.STOP


def test_historical_spend_does_not_trigger_stop_when_opening_spent_subtracted() -> None:
    # На аккаунте потрачено 5 000 000 ₽ до начала периода, лимит периода 1 000 000 ₽.
    # В текущем периоде потрачено только 100 000 ₽.
    snapshot = fresh(5_100_000)
    verdict = evaluate_watch(PERIOD, snapshot, ZERO, NOW, RULES, opening_spent=5_000_000)
    assert verdict.action is WatchAction.OK
    assert verdict.projected == 100_000
