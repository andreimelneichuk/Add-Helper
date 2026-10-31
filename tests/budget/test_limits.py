from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.domain.limits import (
    BudgetPeriod,
    CampaignBudget,
    LimitChange,
    RejectionReason,
    check_limit_change,
)

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RULES = load_rules("budget-1")
PERIOD = BudgetPeriod(hard_limit=1_000_000_000, reserve_bp=1000)


def campaign(
    campaign_id: str = "a",
    limit: int = 300_000_000,
    spent: int = 0,
    active: bool = True,
    last_increase_at: datetime | None = None,
) -> CampaignBudget:
    return CampaignBudget(campaign_id, limit, spent, active, last_increase_at)


def test_operating_budget_is_hard_limit_minus_reserve() -> None:
    assert PERIOD.operating_budget == 900_000_000


def test_float_money_is_rejected() -> None:
    with pytest.raises(TypeError):
        BudgetPeriod(hard_limit=100.5, reserve_bp=1000)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        CampaignBudget("a", 100.0, 0, True, None)  # type: ignore[arg-type]


@given(st.integers(0, 10**15), st.integers(0, 10_000))
def test_operating_budget_never_exceeds_hard_limit(hard: int, reserve_bp: int) -> None:
    period = BudgetPeriod(hard_limit=hard, reserve_bp=reserve_bp)
    assert 0 <= period.operating_budget <= hard
    # Округление идёт в сторону безопасности: резерв не меньше заявленного.
    assert (hard - period.operating_budget) * 10_000 >= hard * reserve_bp


def test_increase_within_rules_is_allowed_and_records_inputs() -> None:
    decision = check_limit_change(
        PERIOD, [campaign()], LimitChange("a", 360_000_000), 0, NOW, RULES
    )

    assert decision.allowed
    assert decision.rules_version == "budget-1"
    assert decision.spent == 0
    assert decision.worst_active == 300_000_000
    # 35% от 300 млн + 35% от 360 млн
    assert decision.worst_change == 231_000_000
    assert decision.limit_sum_after == 360_000_000


def test_increase_over_operating_budget_is_rejected() -> None:
    campaigns = [campaign("a", 800_000_000), campaign("b", 90_000_000, active=False)]

    decision = check_limit_change(PERIOD, campaigns, LimitChange("b", 108_000_000), 0, NOW, RULES)

    assert decision.reason is RejectionReason.EXCEEDS_OPERATING_BUDGET


def test_increase_with_worst_case_over_hard_limit_is_rejected() -> None:
    decision = check_limit_change(
        PERIOD, [campaign(limit=700_000_000)], LimitChange("a", 800_000_000), 0, NOW, RULES
    )

    assert decision.reason is RejectionReason.WORST_CASE_EXCEEDS_HARD_LIMIT


def test_spend_correction_counts_against_worst_case() -> None:
    change = LimitChange("a", 360_000_000)

    assert check_limit_change(PERIOD, [campaign()], change, 460_000_000, NOW, RULES).allowed
    late = check_limit_change(PERIOD, [campaign()], change, 470_000_000, NOW, RULES)
    assert late.reason is RejectionReason.WORST_CASE_EXCEEDS_HARD_LIMIT


def test_increase_step_is_limited() -> None:
    decision = check_limit_change(
        PERIOD, [campaign()], LimitChange("a", 360_000_001), 0, NOW, RULES
    )

    assert decision.reason is RejectionReason.STEP_TOO_LARGE


def test_increase_is_limited_to_once_a_week() -> None:
    recent = campaign(last_increase_at=NOW - timedelta(days=6, hours=23))
    settled = campaign(last_increase_at=NOW - timedelta(days=7))
    change = LimitChange("a", 330_000_000)

    assert (
        check_limit_change(PERIOD, [recent], change, 0, NOW, RULES).reason
        is RejectionReason.TOO_SOON
    )
    assert check_limit_change(PERIOD, [settled], change, 0, NOW, RULES).allowed


def test_decrease_is_allowed_even_when_budget_is_already_broken() -> None:
    """Снижение уменьшает риск, поэтому проверка худшего случая его не блокирует."""
    decision = check_limit_change(
        PERIOD,
        [campaign(limit=2_000_000_000, spent=950_000_000)],
        LimitChange("a", 1_000_000_000),
        950_000_000,
        NOW,
        RULES,
    )

    assert decision.allowed


def test_limit_below_spent_is_rejected() -> None:
    decision = check_limit_change(
        PERIOD, [campaign(spent=100_000_000)], LimitChange("a", 99_999_999), 0, NOW, RULES
    )

    assert decision.reason is RejectionReason.BELOW_SPENT


def test_unknown_campaign_is_rejected() -> None:
    decision = check_limit_change(PERIOD, [campaign()], LimitChange("x", 1), 0, NOW, RULES)

    assert decision.reason is RejectionReason.UNKNOWN_CAMPAIGN


campaign_lists = st.lists(
    st.builds(
        CampaignBudget,
        st.uuids().map(str),
        st.integers(0, 10**12),
        st.integers(0, 10**12),
        st.booleans(),
        st.none(),
    ),
    min_size=1,
    max_size=6,
)


@given(
    campaign_lists,
    st.integers(0, 10**13),
    st.integers(0, 10**12),
    st.integers(0, 10**13),
    st.integers(0, 5),
    st.integers(0, 100_000),
)
def test_allowed_increase_never_breaks_budget_formula(
    campaigns: list[CampaignBudget],
    hard: int,
    spent: int,
    new_limit: int,
    index: int,
    max_increase_bp: int,
) -> None:
    rules = replace(RULES, max_increase_bp=max_increase_bp)
    period = BudgetPeriod(hard_limit=hard, reserve_bp=1000)
    target = campaigns[index % len(campaigns)]
    change = LimitChange(target.campaign_id, new_limit)

    decision = check_limit_change(period, campaigns, change, spent, NOW, rules)

    if decision.allowed and new_limit > target.limit:
        assert decision.limit_sum_after <= period.operating_budget
        assert spent + decision.worst_active + decision.worst_change <= hard
        assert decision.worst_change >= new_limit - target.limit
