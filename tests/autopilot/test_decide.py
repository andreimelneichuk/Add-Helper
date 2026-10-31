"""Предложения и ограничения: docs/04-autopilot.md §4 «Действия и ограничения»."""

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
