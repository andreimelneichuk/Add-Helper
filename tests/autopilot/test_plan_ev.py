"""Решения autopilot-3 по ожидаемой потере (docs/16, §11; docs/04, §4)."""

import dataclasses
from datetime import UTC, datetime
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.domain.decide import ActionKind, BlockReason, CyclePlan, Mode
from adhelper.autopilot.domain.health import HealthReason, HealthReport
from adhelper.autopilot.domain.model import AdStats, CampaignStats, Verdict
from adhelper.autopilot.domain.plan_ev import plan_cycle_ev

RULES = load_rules("autopilot-3")
TARGET = 300_000_000
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TRUSTED = HealthReport(())
# 400 кликов за неделю зрелых данных: возраст = 7 дней зрелости + 2 дня на дозревание.
AGE = 9


def ad(
    ref: str,
    clicks: int,
    leads: int,
    *,
    campaign: str = "c1",
    age: int = AGE,
    spend: int | None = None,
    **extra: bool,
) -> AdStats:
    cost = spend if spend is not None else clicks * 5_000_000
    return AdStats(ref, campaign, ref, cost, leads, age, True, clicks=clicks, **extra)


def plan(
    ads: list[AdStats],
    campaigns: list[CampaignStats] | None = None,
    *,
    health: HealthReport = TRUSTED,
    mode: Mode = Mode.CONFIRM,
) -> CyclePlan:
    return plan_cycle_ev(ads, campaigns or [], TARGET, health, mode, NOW, RULES)


def test_rules_file_is_the_expected_loss_engine_with_its_own_version() -> None:
    assert RULES.version == "autopilot-3"
    assert RULES.engine == "expected_loss"
    assert RULES.min_weekly_leads == 10 and RULES.max_variants == 2


def test_a_20_of_400_against_b_10_of_400_proposes_pausing_b_with_numbers() -> None:
    result = plan([ad("ad:a", 400, 20), ad("ad:b", 400, 10)])

    [action] = result.actions
    assert action.kind is ActionKind.PAUSE_AD and action.ref == "ad:b"
    assert action.needs_confirmation is True
    assert action.rules_version == "autopilot-3"
    facts = action.facts
    assert 9000 <= facts["p_worse_bp"] <= 9999
    assert facts["loss_micros"] > facts["error_cost_micros"] > 0
    assert facts["clicks"] == 400 and facts["leads"] == 10 and facts["target_micros"] == TARGET
    assert facts["weekly_clicks"] == 400
    assert 0 < facts["cpl_low_micros"] < facts["cpl_high_micros"]
    assert all(isinstance(value, int) for value in facts.values())


def test_b_3_of_100_means_wait_not_act() -> None:
    result = plan([ad("ad:a", 400, 20), ad("ad:b", 100, 3)])

    assert result.actions == []
    assert result.blocked["ad:b"] is BlockReason.LOW_DATA
    assert result.evaluations["ad:b"].verdict is Verdict.LOW_DATA


def test_not_enough_clicks_never_acts_even_with_terrible_numbers() -> None:
    result = plan([ad("ad:a", 400, 40), ad("ad:b", 20, 0)])

    assert result.actions == []
    assert result.blocked["ad:b"] is BlockReason.LOW_DATA


def test_small_difference_is_not_worth_acting_on() -> None:
    result = plan([ad("ad:a", 400, 20), ad("ad:b", 400, 18)])

    assert result.actions == []
    assert "ad:b" in result.blocked


def test_cheap_to_wait_when_loss_is_below_cost_of_a_mistake() -> None:
    # Медленный поток: за неделю уходит около сотни кликов, недобор дешевле трёх целевых цен.
    slow = plan([ad("ad:a", 800, 80, age=90), ad("ad:b", 800, 50, age=90)])

    assert slow.actions == []
    assert slow.blocked["ad:b"] is BlockReason.WAIT_IS_CHEAPER
    facts = slow.assessments["ad:b"]
    assert 0 < facts["loss_micros"] <= facts["error_cost_micros"]


def test_young_ad_costs_more_to_lose_so_it_waits_longer() -> None:
    old = plan([ad("ad:a", 100, 10), ad("ad:b", 100, 4)])
    young = plan([ad("ad:a", 100, 10, age=6), ad("ad:b", 100, 4, age=6)])

    assert old.actions
    assert young.actions == [] and young.blocked["ad:b"] is BlockReason.WAIT_IS_CHEAPER
    assert (
        young.assessments["ad:b"]["error_cost_micros"]
        == 3 * old.assessments["ad:b"]["error_cost_micros"]
    )


def test_three_variants_in_a_direction_are_not_compared() -> None:
    result = plan([ad("ad:a", 500, 30), ad("ad:b", 400, 10), ad("ad:c", 300, 9)])

    # Сравниваем двух самых посещаемых, третий ждёт.
    assert result.blocked["ad:c"] is BlockReason.TOO_MANY_VARIANTS
    assert all(action.ref != "ad:c" for action in result.actions)


def test_slow_direction_only_protects_and_does_not_optimize() -> None:
    # 6 обращений за неделю: меньше порога обучения стратегии.
    result = plan([ad("ad:a", 400, 5), ad("ad:b", 400, 1)])

    assert result.actions == []
    assert set(result.blocked.values()) == {BlockReason.FEW_LEADS}


def test_untrusted_data_is_protect_only_even_if_numbers_scream() -> None:
    # Сценарий октябрьского сбоя Директа: данные плохие, выводов об объявлениях нет.
    health = HealthReport((HealthReason.PLATFORM_INCIDENT,))
    result = plan([ad("ad:a", 400, 40), ad("ad:b", 400, 0)], health=health)

    assert result.protect_only is True
    assert result.actions == [] and result.evaluations == {}


def test_pause_mode_never_acts() -> None:
    assert plan([ad("ad:a", 400, 20), ad("ad:b", 400, 10)], mode=Mode.PAUSE).actions == []


def test_auto_mode_does_not_need_confirmation() -> None:
    [action] = plan([ad("ad:a", 400, 20), ad("ad:b", 400, 10)], mode=Mode.AUTO).actions
    assert action.needs_confirmation is False


def test_the_only_variant_in_a_direction_stays() -> None:
    only = plan([ad("ad:b", 400, 10)])
    assert only.actions == [] and only.blocked["ad:b"] is BlockReason.LAST_AD_IN_GROUP


def test_recent_change_and_running_test_block_the_decision() -> None:
    ads = [ad("ad:a", 400, 20), ad("ad:b", 400, 10)]
    recent = CampaignStats("c1", 1_000_000_000, False, NOW)
    assert plan(ads, [recent]).blocked["ad:b"] is BlockReason.CHANGED_RECENTLY
    testing = CampaignStats("c1", 1_000_000_000, False, None, test_running=True)
    assert plan(ads, [testing]).blocked["ad:b"] is BlockReason.TEST_RUNNING


def test_owner_rollback_is_respected() -> None:
    result = plan([ad("ad:a", 400, 20), ad("ad:b", 400, 10, rollback_hold=True)])
    assert result.actions == [] and result.blocked["ad:b"] is BlockReason.ROLLED_BACK


def test_only_one_change_per_direction_and_directions_are_independent() -> None:
    ads = [
        ad("ad:a", 400, 20),
        ad("ad:b", 400, 10),
        ad("ad:x", 400, 20, campaign="c2"),
        ad("ad:y", 400, 10, campaign="c2"),
    ]
    assert sorted(action.ref for action in plan(ads).actions) == ["ad:b", "ad:y"]


def test_inactive_ads_are_ignored() -> None:
    off = dataclasses.replace(ad("ad:c", 900, 90), active=False)
    result = plan([ad("ad:a", 400, 20), ad("ad:b", 400, 10), off])
    assert [action.ref for action in result.actions] == ["ad:b"]


@settings(max_examples=60, deadline=None)
@given(
    clicks_a=st.integers(0, 3000),
    leads_a=st.integers(0, 200),
    clicks_b=st.integers(0, 3000),
    leads_b=st.integers(0, 200),
    age=st.integers(0, 40),
)
def test_decisions_are_reproducible_and_every_action_carries_its_proof(
    clicks_a: int, leads_a: int, clicks_b: int, leads_b: int, age: int
) -> None:
    ads = [
        ad("ad:a", clicks_a, min(leads_a, clicks_a), age=age),
        ad("ad:b", clicks_b, min(leads_b, clicks_b), age=age),
    ]
    first, again = plan(ads), plan(ads)
    assert first == again
    for action in first.actions:
        facts = action.facts
        assert facts["p_worse_bp"] >= RULES.min_worse_probability_bp
        assert facts["loss_micros"] > facts["error_cost_micros"]
        assert facts["clicks"] >= RULES.min_clicks
        assert facts["weekly_leads"] >= RULES.min_weekly_leads


@settings(max_examples=40, deadline=None)
@given(clicks=st.integers(0, 49), leads=st.integers(0, 49))
def test_below_minimum_clicks_there_is_never_an_action(clicks: int, leads: int) -> None:
    result = plan([ad("ad:a", 900, 60), ad("ad:b", clicks, min(leads, clicks))])
    assert result.actions == []


INPUT_KEYS = {
    "clicks",
    "leads",
    "spend_micros",
    "age_days",
    "weekly_leads",
    "weekly_clicks",
    "group_clicks",
    "group_leads",
    "prior_clicks",
    "test_running",
    "last_change_at",
}


def test_every_branch_records_all_inputs_of_the_decision() -> None:
    slow = plan([ad("ad:a", 400, 5), ad("ad:b", 400, 1)])
    single = plan([ad("ad:a", 400, 20)])
    wait = plan([ad("ad:a", 400, 20), ad("ad:b", 100, 3)])
    full = plan([ad("ad:a", 400, 20), ad("ad:b", 400, 10)])
    for result in (slow, single, wait, full):
        for ref, facts in result.assessments.items():
            assert set(facts) >= INPUT_KEYS, (ref, INPUT_KEYS - set(facts))
    facts = full.assessments["ad:b"]
    assert facts["prior_clicks"] == RULES.prior_clicks and facts["group_clicks"] == 800
    assert facts["group_leads"] == 30 and facts["expected_gap_ppm"] > 0 and facts["p_worse_bp"] > 0


def test_campaign_state_is_part_of_the_recorded_inputs() -> None:
    changed = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    campaign = CampaignStats("c1", 1, False, changed, test_running=True)
    result = plan([ad("ad:a", 400, 20), ad("ad:b", 400, 10)], [campaign])
    facts = result.assessments["ad:b"]
    assert facts["test_running"] == 1 and facts["last_change_at"] == int(changed.timestamp())


def test_more_leads_than_clicks_is_contradictory_data_and_only_protects() -> None:
    result = plan([ad("ad:a", 400, 20), ad("ad:b", 100, 120)])
    assert result.actions == []
    assert set(result.blocked.values()) == {BlockReason.DATA_CONFLICT}
    assert result.assessments["ad:b"]["data_conflict"] == 1


def test_rules_reject_nonsense_thresholds() -> None:
    import pytest

    bad_values: tuple[dict[str, Any], ...] = (
        {"prior_clicks": 0},
        {"interval_tail_bp": 0},
        {"interval_tail_bp": 5000},
        {"min_worse_probability_bp": 10000},
        {"max_variants": 1},
        {"min_weekly_leads": -1},
    )
    for bad in bad_values:
        with pytest.raises(ValueError):
            dataclasses.replace(RULES, **bad)
