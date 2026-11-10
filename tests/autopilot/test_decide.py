"""Предложения и ограничения."""

import dataclasses
from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.domain.decide import ActionKind, BlockReason, Mode, plan_cycle
from adhelper.autopilot.domain.health import HealthReason, HealthReport
from adhelper.autopilot.domain.model import AdStats, CampaignStats

RULES = load_rules("autopilot-1")
TARGET = 400_000_000
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TRUSTED = HealthReport(())
BAD_SPEND = TARGET * 5
GOOD_SPEND = TARGET * 10


def bad(ref: str = "ad:bad", group: str = "g1", campaign: str = "c1") -> AdStats:
    return AdStats(ref, campaign, group, BAD_SPEND, 0, 30, active=True)


def good(ref: str = "ad:good", group: str = "g1", campaign: str = "c1") -> AdStats:
    return AdStats(ref, campaign, group, GOOD_SPEND, 20, 30, active=True)


def limited(limit: int = 1_000_000_000) -> CampaignStats:
    return CampaignStats("c1", limit, budget_limited=True, last_change_at=None)


def test_inefficient_ad_is_paused_with_numbers_in_facts() -> None:
    plan = plan_cycle([bad(), good()], [], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)

    action = next(a for a in plan.actions if a.kind is ActionKind.PAUSE_AD)
    assert action.ref == "ad:bad"
    assert action.facts["spend_micros"] == BAD_SPEND
    assert action.facts["leads"] == 0
    assert action.facts["target_micros"] == TARGET
    assert action.rules_version == RULES.version


def test_last_working_ad_in_group_stays() -> None:
    plan = plan_cycle([bad()], [], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)

    assert not [a for a in plan.actions if a.kind is ActionKind.PAUSE_AD]
    assert plan.blocked == {"ad:bad": BlockReason.LAST_AD_IN_GROUP}


def test_two_bad_ads_leave_one_running() -> None:
    plan = plan_cycle(
        [bad("ad:1"), bad("ad:2", campaign="c1"), bad("ad:3", group="g2", campaign="c2")],
        [],
        TARGET,
        TRUSTED,
        Mode.AUTO,
        NOW,
        RULES,
    )
    paused = [a.ref for a in plan.actions if a.kind is ActionKind.PAUSE_AD]
    assert paused == ["ad:1"]


def test_only_one_material_change_per_campaign_per_cycle() -> None:
    plan = plan_cycle(
        [bad("ad:1"), bad("ad:2"), good()], [limited()], TARGET, TRUSTED, Mode.AUTO, NOW, RULES
    )
    assert len([a for a in plan.actions if a.campaign_id == "c1"]) == 1


def test_recent_change_blocks_campaign_for_three_days() -> None:
    recent = CampaignStats("c1", 10**9, True, NOW - timedelta(days=2, hours=23))
    plan = plan_cycle([bad(), good()], [recent], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert plan.actions == []
    assert plan.blocked["ad:bad"] is BlockReason.CHANGED_RECENTLY

    old = CampaignStats("c1", 10**9, True, NOW - timedelta(days=3))
    plan = plan_cycle([bad(), good()], [old], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert plan.actions


def test_budget_grows_only_if_campaign_is_limited_by_budget() -> None:
    grow = plan_cycle([good()], [limited()], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    action = grow.actions[0]
    assert action.kind is ActionKind.INCREASE_LIMIT
    assert action.ref == "c1"
    assert action.new_limit == 1_200_000_000

    unlimited = CampaignStats("c1", 10**9, False, None)
    plan = plan_cycle([good()], [unlimited], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert plan.actions == []


def test_increase_is_integer_and_rounds_down() -> None:
    plan = plan_cycle([good()], [limited(1_000_003)], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert plan.actions[0].new_limit == 1_000_003 + 200_000


def test_borderline_starts_test_unless_campaign_already_tests() -> None:
    middle = AdStats("ad:m", "c1", "g1", TARGET * 3, 3, 30, active=True)
    plan = plan_cycle([middle], [], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert [a.kind for a in plan.actions] == [ActionKind.START_TEST]

    busy = CampaignStats("c1", 10**9, False, None, test_running=True)
    plan = plan_cycle([middle], [busy], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert plan.actions == []


def test_ads_in_running_test_are_not_touched() -> None:
    busy = CampaignStats("c1", 10**9, False, None, test_running=True)
    plan = plan_cycle([bad(), good()], [busy], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert plan.actions == []
    assert plan.blocked["ad:bad"] is BlockReason.TEST_RUNNING


def test_untrusted_data_gives_no_conclusions_at_all() -> None:
    report = HealthReport((HealthReason.PLATFORM_INCIDENT,))
    plan = plan_cycle([bad(), good()], [limited()], TARGET, report, Mode.AUTO, NOW, RULES)
    assert plan.protect_only
    assert plan.actions == []
    assert plan.evaluations == {}


def test_confirm_mode_marks_money_actions_for_approval() -> None:
    plan = plan_cycle([bad(), good()], [], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES)
    assert plan.actions
    assert all(action.needs_confirmation for action in plan.actions)


def test_pause_mode_observes_but_does_not_act() -> None:
    plan = plan_cycle([bad(), good()], [limited()], TARGET, TRUSTED, Mode.PAUSE, NOW, RULES)
    assert plan.actions == []
    assert plan.evaluations


def test_already_paused_ad_is_ignored() -> None:
    paused = AdStats("ad:p", "c1", "g1", BAD_SPEND, 0, 30, active=False)
    plan = plan_cycle([paused, good()], [], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert not [a for a in plan.actions if a.ref == "ad:p"]


@given(
    st.lists(
        st.tuples(st.integers(0, 3), st.integers(0, TARGET * 20), st.integers(0, 40)),
        min_size=1,
        max_size=12,
    )
)
def test_every_group_keeps_a_running_ad(rows: list[tuple[int, int, int]]) -> None:
    ads = [
        AdStats(f"ad:{i}", f"c{group}", f"g{group}", spend, leads, 30, active=True)
        for i, (group, spend, leads) in enumerate(rows)
    ]
    plan = plan_cycle(ads, [], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)

    paused = {a.ref for a in plan.actions if a.kind is ActionKind.PAUSE_AD}
    for group in {ad.group_id for ad in ads}:
        assert [ad for ad in ads if ad.group_id == group and ad.ref not in paused]


def test_same_input_gives_same_plan() -> None:
    ads = [bad(), good()]
    first = plan_cycle(ads, [limited()], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert plan_cycle(ads, [limited()], TARGET, TRUSTED, Mode.AUTO, NOW, RULES) == first


def test_action_undone_by_user_is_not_repeated() -> None:
    held = AdStats("ad:bad", "c1", "g1", BAD_SPEND, 0, 30, active=True, rollback_hold=True)
    plan = plan_cycle([held, good()], [], TARGET, TRUSTED, Mode.AUTO, NOW, RULES)
    assert not [a for a in plan.actions if a.ref == "ad:bad"]
    assert plan.blocked["ad:bad"] is BlockReason.ROLLED_BACK


RULES_2 = load_rules("autopilot-2")


def middle(ref: str = "ad:m", hold: bool = False) -> AdStats:
    return AdStats(ref, "c1", "g1", TARGET * 3, 3, 30, active=True, test_hold=hold)


def daily(limit: int) -> CampaignStats:
    return CampaignStats("c1", limit, budget_limited=False, last_change_at=None)


def test_test_is_proposed_when_budget_covers_minimal_volume() -> None:
    # 7 дней по лимиту должны покрыть 4 цены обращения: иначе тест не даст вывода.
    enough = -(-TARGET * RULES_2.test_min_target_multiple // RULES_2.test_days)
    plan = plan_cycle([middle()], [daily(enough)], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES_2)

    action = plan.actions[0]
    assert action.kind is ActionKind.START_TEST
    assert action.needs_confirmation
    assert action.facts["limit_micros"] == enough
    assert action.rules_version == "autopilot-2"


def test_small_budget_gives_no_test_and_says_why() -> None:
    small = -(-TARGET * RULES_2.test_min_target_multiple // RULES_2.test_days) - 1
    plan = plan_cycle([middle()], [daily(small)], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES_2)

    assert plan.actions == []
    assert plan.blocked == {"ad:m": BlockReason.BUDGET_TOO_SMALL}


def test_unknown_budget_gives_no_test() -> None:
    plan = plan_cycle([middle()], [], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES_2)
    assert plan.actions == []
    assert plan.blocked == {"ad:m": BlockReason.BUDGET_TOO_SMALL}


def test_declined_test_is_not_proposed_again_while_on_hold() -> None:
    plan = plan_cycle(
        [middle(hold=True), middle("ad:n")],
        [daily(10**10)],
        TARGET,
        TRUSTED,
        Mode.CONFIRM,
        NOW,
        RULES_2,
    )
    assert [a.ref for a in plan.actions] == ["ad:n"]
    assert plan.blocked["ad:m"] is BlockReason.TEST_DECLINED


def test_test_waits_for_recent_change_like_other_actions() -> None:
    changed = CampaignStats("c1", 10**10, False, NOW - timedelta(days=1))
    plan = plan_cycle([middle()], [changed], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES_2)
    assert plan.actions == []


def test_first_version_keeps_its_decisions() -> None:
    # Прежние решения воспроизводимы: autopilot-1 не знал проверки бюджета теста.
    plan = plan_cycle([middle()], [], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES)
    assert [a.kind for a in plan.actions] == [ActionKind.START_TEST]


def test_no_test_right_after_change_and_reason_is_named() -> None:
    # После изменения данные смешаны: тест подождёт, а журнал назовёт причину.
    changed = CampaignStats("c1", 10**10, budget_limited=False, last_change_at=NOW)
    plan = plan_cycle([middle()], [changed], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES_2)
    assert plan.actions == []
    assert plan.blocked == {"ad:m": BlockReason.CHANGED_RECENTLY}


def test_no_second_test_while_one_runs_and_reason_is_named() -> None:
    running = CampaignStats("c1", 10**10, budget_limited=False, last_change_at=None)
    running = dataclasses.replace(running, test_running=True)
    plan = plan_cycle([middle()], [running], TARGET, TRUSTED, Mode.CONFIRM, NOW, RULES_2)
    assert plan.actions == []
    assert plan.blocked == {"ad:m": BlockReason.TEST_RUNNING}
