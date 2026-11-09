"""Политика автопилота: жёсткие рамки автоматического действия (docs/04-autopilot.md §7)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.autopilot.adapters.rules_file import load_policy_rules
from adhelper.autopilot.domain.policy import (
    PolicyAction,
    PolicyInput,
    PolicyReason,
    PolicyRules,
    check_policy,
)

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
M = 1_000_000
RULES = load_policy_rules("policy-1")


def base(action: PolicyAction = PolicyAction.PAUSE_AD, **over: object) -> PolicyInput:
    values: dict[str, object] = {
        "action": action,
        "ref": "ad-1",
        "campaign_id": "c1",
        "allowed": frozenset(
            {PolicyAction.PAUSE_AD, PolicyAction.INCREASE_LIMIT, PolicyAction.START_TEST}
        ),
        "now": NOW,
        "changes_today": 0,
        "moved_this_week": 0,
        "amount": 0,
        "operating_budget": 100_000 * M,
        "active_ads_in_group": 3,
        "ad_in_learning": False,
        "campaign_in_learning": False,
        "auto_strategy": False,
        "last_limit_change_at": None,
        "data_alert": False,
        "api_points_rest": 10_000,
    }
    values.update(over)
    return PolicyInput(**values)  # type: ignore[arg-type]


def test_rules_file_is_versioned_and_valid() -> None:
    assert RULES.version == "policy-1"
    assert RULES.max_changes_per_day >= 1
    assert RULES.auto_strategy_interval_days == 14


def test_plain_pause_of_one_of_several_ads_is_allowed_and_records_rules_version() -> None:
    decision = check_policy(base(), RULES)
    assert decision.allowed
    assert decision.reason is None
    assert decision.rules_version == "policy-1"
    assert decision.inputs["active_ads_in_group"] == 3


def test_class_not_allowed_by_settings_is_refused() -> None:
    decision = check_policy(base(allowed=frozenset({PolicyAction.INCREASE_LIMIT})), RULES)
    assert decision.reason is PolicyReason.CLASS_NOT_ALLOWED


def test_campaign_pause_is_always_refused_even_if_listed_as_allowed() -> None:
    inp = base(PolicyAction.PAUSE_CAMPAIGN, allowed=frozenset(PolicyAction))
    assert check_policy(inp, RULES).reason is PolicyReason.CAMPAIGN_PAUSE_FORBIDDEN


@pytest.mark.parametrize("active", [0, 1])
def test_last_ad_in_group_is_not_paused(active: int) -> None:
    decision = check_policy(base(active_ads_in_group=active), RULES)
    assert decision.reason is PolicyReason.LAST_AD_IN_GROUP


def test_unknown_group_size_is_treated_as_last_ad() -> None:
    assert (
        check_policy(base(active_ads_in_group=None), RULES).reason is PolicyReason.LAST_AD_IN_GROUP
    )


def test_daily_change_limit() -> None:
    limit = RULES.max_changes_per_day
    assert check_policy(base(changes_today=limit - 1), RULES).allowed
    assert check_policy(base(changes_today=limit), RULES).reason is PolicyReason.DAILY_LIMIT


def test_object_in_learning_is_not_touched() -> None:
    assert check_policy(base(ad_in_learning=True), RULES).reason is PolicyReason.IN_LEARNING
    inc = base(PolicyAction.INCREASE_LIMIT, amount=1 * M, campaign_in_learning=True)
    assert check_policy(inc, RULES).reason is PolicyReason.IN_LEARNING


def test_unknown_learning_state_of_campaign_is_treated_as_learning() -> None:
    inc = base(PolicyAction.INCREASE_LIMIT, amount=1 * M, campaign_in_learning=None)
    assert check_policy(inc, RULES).reason is PolicyReason.IN_LEARNING


def test_data_alert_blocks_everything() -> None:
    assert check_policy(base(data_alert=True), RULES).reason is PolicyReason.DATA_ALERT


def test_api_points_shortage_blocks_but_unknown_points_do_not() -> None:
    low = base(api_points_rest=RULES.min_api_points - 1)
    assert check_policy(low, RULES).reason is PolicyReason.API_POINTS_LOW
    assert check_policy(base(api_points_rest=None), RULES).allowed


def test_limit_rise_waits_14_days_on_auto_strategy() -> None:
    recent = NOW - timedelta(days=13)
    inc = base(PolicyAction.INCREASE_LIMIT, amount=1 * M, auto_strategy=True)
    assert (
        check_policy(replace(inc, last_limit_change_at=recent), RULES).reason
        is PolicyReason.AUTO_STRATEGY_INTERVAL
    )
    ok = replace(inc, last_limit_change_at=NOW - timedelta(days=14))
    assert check_policy(ok, RULES).allowed


def test_unknown_strategy_is_treated_as_auto_strategy() -> None:
    inc = base(
        PolicyAction.INCREASE_LIMIT,
        amount=1 * M,
        auto_strategy=None,
        last_limit_change_at=NOW - timedelta(days=3),
    )
    assert check_policy(inc, RULES).reason is PolicyReason.AUTO_STRATEGY_INTERVAL


def test_manual_strategy_is_not_held_for_14_days() -> None:
    inc = base(
        PolicyAction.INCREASE_LIMIT,
        amount=1 * M,
        auto_strategy=False,
        last_limit_change_at=NOW - timedelta(days=1),
    )
    assert check_policy(inc, RULES).allowed


def test_weekly_move_share_is_capped_by_operating_budget() -> None:
    cap = RULES.max_weekly_move_bp * 100_000 * M // 10_000
    inc = base(PolicyAction.INCREASE_LIMIT, amount=1 * M, moved_this_week=cap - 1 * M)
    assert check_policy(inc, RULES).allowed
    over = replace(inc, moved_this_week=cap - 1 * M + 1)
    assert check_policy(over, RULES).reason is PolicyReason.WEEKLY_SHARE


def test_transfer_is_governed_like_a_limit_rise_and_needs_the_increase_class() -> None:
    transfer = base(PolicyAction.TRANSFER_LIMIT, amount=1 * M, auto_strategy=False)
    assert check_policy(transfer, RULES).allowed
    no_class = replace(transfer, allowed=frozenset({PolicyAction.PAUSE_AD}))
    assert check_policy(no_class, RULES).reason is PolicyReason.CLASS_NOT_ALLOWED


def test_non_positive_amount_for_money_action_is_refused() -> None:
    inc = base(PolicyAction.INCREASE_LIMIT, amount=0)
    assert check_policy(inc, RULES).reason is PolicyReason.BAD_AMOUNT


def test_rules_reject_nonsense_thresholds() -> None:
    with pytest.raises(ValueError):
        PolicyRules(
            version="x",
            max_changes_per_day=0,
            max_weekly_move_bp=2000,
            auto_strategy_interval_days=14,
            min_group_active_ads=2,
            ad_learning_days=7,
            min_api_points=500,
        )


@given(
    changes=st.integers(0, 50),
    moved=st.integers(0, 10**15),
    amount=st.integers(-(10**12), 10**12),
    budget=st.integers(0, 10**13),
    action=st.sampled_from(list(PolicyAction)),
    alert=st.booleans(),
)
def test_decision_is_deterministic_and_allowed_means_no_reason(
    changes: int, moved: int, amount: int, budget: int, action: PolicyAction, alert: bool
) -> None:
    inp = base(
        action,
        changes_today=changes,
        moved_this_week=moved,
        amount=amount,
        operating_budget=budget,
        data_alert=alert,
    )
    first, second = check_policy(inp, RULES), check_policy(inp, RULES)
    assert first == second
    assert first.allowed == (first.reason is None)
    if action is PolicyAction.PAUSE_CAMPAIGN or alert:
        assert not first.allowed


def test_auto_strategy_with_unknown_last_change_is_refused_not_allowed() -> None:
    for strategy in (True, None):
        decision = check_policy(
            base(
                PolicyAction.INCREASE_LIMIT,
                amount=M,
                auto_strategy=strategy,
                last_limit_change_at=None,
            ),
            RULES,
        )
        assert decision.reason is PolicyReason.AUTO_STRATEGY_INTERVAL


def test_manual_strategy_with_no_change_history_may_rise() -> None:
    decision = check_policy(base(PolicyAction.INCREASE_LIMIT, amount=M, auto_strategy=False), RULES)
    assert decision.allowed


def test_pause_of_ad_in_a_campaign_known_to_be_learning_is_refused() -> None:
    assert check_policy(base(campaign_in_learning=True), RULES).reason is PolicyReason.IN_LEARNING
    # Неизвестное обучение кампании паузе объявления не мешает: решает возраст самого объявления.
    assert check_policy(base(campaign_in_learning=None), RULES).allowed


def test_ad_younger_than_the_learning_threshold_is_refused_by_the_policy_itself() -> None:
    young = RULES.ad_learning_days - 1
    assert check_policy(base(ad_age_days=young), RULES).reason is PolicyReason.IN_LEARNING
    assert check_policy(base(ad_age_days=RULES.ad_learning_days), RULES).allowed
