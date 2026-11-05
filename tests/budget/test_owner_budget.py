"""Решения владельца о деньгах: снятие паузы и смена бюджета месяца."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, RejectionReason
from adhelper.budget.domain.owner import check_resume, minimum_budget, plan_budget_change

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
PERIOD = BudgetPeriod(hard_limit=1_000_000, reserve_bp=1000, ends_at=NOW + timedelta(days=20))


def campaign(cid: str, limit: int, spent: int = 0, active: bool = True) -> CampaignBudget:
    return CampaignBudget(cid, limit, spent, active, None)


def worst_case(spent: int, campaigns: list[CampaignBudget]) -> int:
    return spent + sum(max(c.limit - c.spent, 0) for c in campaigns if c.active)


def test_resume_within_budget_is_allowed() -> None:
    campaigns = [campaign("a", 400_000, 100_000), campaign("b", 300_000, 50_000, active=False)]
    assert check_resume(PERIOD, campaigns, "b", 150_000, NOW) is None


def test_resume_refused_when_worst_case_exceeds_hard_limit() -> None:
    # Пока кампания стояла, деньги ушли: её остаток лимита уже не помещается в бюджет.
    campaigns = [campaign("a", 500_000, 450_000), campaign("b", 400_000, 0, active=False)]
    reason = check_resume(PERIOD, campaigns, "b", 700_000, NOW)
    assert reason is RejectionReason.WORST_CASE_EXCEEDS_HARD_LIMIT


def test_resume_refused_when_limits_exceed_operating_budget() -> None:
    campaigns = [campaign("a", 600_000), campaign("b", 400_000, active=False)]
    reason = check_resume(PERIOD, campaigns, "b", 0, NOW)
    assert reason is RejectionReason.EXCEEDS_OPERATING_BUDGET


def test_resume_refused_after_period_end_and_for_unknown_campaign() -> None:
    campaigns = [campaign("b", 100_000, active=False)]
    assert check_resume(PERIOD, campaigns, "b", 0, NOW + timedelta(days=21)) is (
        RejectionReason.PERIOD_ENDED
    )
    assert check_resume(PERIOD, campaigns, "x", 0, NOW) is RejectionReason.UNKNOWN_CAMPAIGN


campaigns_st = st.lists(
    st.tuples(st.integers(0, 10**9), st.integers(0, 10**9), st.booleans()),
    min_size=1,
    max_size=6,
).map(
    lambda rows: [
        campaign(str(i), max(limit, spent), spent, active)
        for i, (limit, spent, active) in enumerate(rows)
    ]
)


@given(campaigns_st, st.integers(0, 10**10), st.integers(0, 10**9), st.integers(0, 5))
def test_allowed_resume_keeps_worst_case_within_budget(
    campaigns: list[CampaignBudget], hard: int, lag: int, pick: int
) -> None:
    period = BudgetPeriod(hard_limit=hard, reserve_bp=1000)
    target = campaigns[pick % len(campaigns)]
    spent = sum(c.spent for c in campaigns) + lag
    if check_resume(period, campaigns, target.campaign_id, spent, NOW) is not None:
        return
    after = [
        CampaignBudget(c.campaign_id, c.limit, c.spent, True, None)
        if c.campaign_id == target.campaign_id
        else c
        for c in campaigns
    ]
    assert sum(c.limit for c in after) <= period.operating_budget
    assert worst_case(spent, after) <= hard


def test_budget_below_spent_is_refused_with_minimum() -> None:
    campaigns = [campaign("a", 500_000, 450_000)]
    plan = plan_budget_change(PERIOD, campaigns, 400_000, 450_000)
    assert plan.refused
    assert plan.minimum == minimum_budget(450_000, PERIOD.reserve_bp)
    assert BudgetPeriod(plan.minimum, PERIOD.reserve_bp).operating_budget >= 450_000
    assert BudgetPeriod(plan.minimum - 1, PERIOD.reserve_bp).operating_budget < 450_000


def test_raising_budget_changes_no_limits() -> None:
    campaigns = [campaign("a", 500_000, 100_000)]
    plan = plan_budget_change(PERIOD, campaigns, 2_000_000, 100_000)
    assert not plan.refused
    assert plan.cuts == ()
    assert plan.period.hard_limit == 2_000_000
    assert plan.period.ends_at == PERIOD.ends_at


def test_lowering_budget_cuts_limits_down_to_new_operating_budget() -> None:
    campaigns = [campaign("a", 600_000, 200_000), campaign("b", 300_000, 100_000)]
    plan = plan_budget_change(PERIOD, campaigns, 500_000, 300_000)
    cuts = {c.campaign_id: c.new_limit for c in plan.cuts}
    assert set(cuts) == {"a", "b"}
    assert sum(cuts.values()) <= plan.period.operating_budget
    assert cuts["a"] >= 200_000 and cuts["b"] >= 100_000


@given(
    campaigns_st,
    st.integers(0, 10**10),
    st.integers(0, 10**10),
    st.integers(0, 9_999),
    st.integers(0, 10**9),
)
def test_accepted_budget_fits_limits_and_worst_case(
    campaigns: list[CampaignBudget], old_hard: int, new_hard: int, reserve_bp: int, lag: int
) -> None:
    period = BudgetPeriod(hard_limit=old_hard, reserve_bp=reserve_bp)
    spent = sum(c.spent for c in campaigns) + lag
    plan = plan_budget_change(period, campaigns, new_hard, spent)
    if plan.refused:
        assert new_hard < plan.minimum
        return
    assert new_hard >= spent
    limits = {c.campaign_id: c.limit for c in campaigns}
    for cut in plan.cuts:
        # Только снижение и не ниже уже потраченного кампанией.
        old = next(c for c in campaigns if c.campaign_id == cut.campaign_id)
        assert old.spent <= cut.new_limit < old.limit
        limits[cut.campaign_id] = cut.new_limit
    after = [
        CampaignBudget(c.campaign_id, limits[c.campaign_id], c.spent, c.active, None)
        for c in campaigns
    ]
    assert sum(limits.values()) <= plan.period.operating_budget
    assert worst_case(spent, after) <= new_hard
