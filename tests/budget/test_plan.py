"""План бюджета до запуска: сумма из мастера, пределы из правил, хранится в сервисе."""

import uuid

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import text

from adhelper.budget.adapters.plan_rules import load_plan_rules
from adhelper.budget.adapters.sql import SqlBudgetPlans
from adhelper.budget.domain.plan import PlanProblem, check_plan
from tests.business.test_site_reads_sql import Sessions, boris  # noqa: F401
from tests.seed import seed_account

RULES = load_plan_rules()


def test_rules_are_sane() -> None:
    assert RULES.version == "plan-2"
    assert 0 < RULES.min_monthly_rubles < RULES.max_monthly_rubles
    assert all(RULES.min_monthly_rubles <= p <= RULES.max_monthly_rubles for p in RULES.presets)
    assert list(RULES.presets) == sorted(RULES.presets)


@given(st.integers(min_value=-(10**12), max_value=10**12))
def test_plan_is_accepted_only_inside_limits(rubles: int) -> None:
    problem = check_plan(rubles, RULES)
    if rubles < RULES.min_monthly_rubles:
        assert problem is PlanProblem.BELOW_MIN
    elif rubles > RULES.max_monthly_rubles:
        assert problem is PlanProblem.ABOVE_MAX
    else:
        assert problem is None


async def test_plan_is_stored_in_micros_and_isolated(
    sessions: Sessions,
    boris: uuid.UUID,  # noqa: F811
) -> None:
    other = uuid.uuid4()
    await seed_account(sessions, other)
    await SqlBudgetPlans(sessions, boris).set(20_000 * 1_000_000, RULES.version)
    await SqlBudgetPlans(sessions, boris).set(25_000 * 1_000_000, RULES.version)
    assert await SqlBudgetPlans(sessions, boris).get() == 25_000 * 1_000_000
    assert await SqlBudgetPlans(sessions, other).get() is None


def test_plan_minimum_covers_launch_minimum() -> None:
    """Сумма, которую принял мастер, не должна упасть на запуске ниже минимума Яндекса."""
    from adhelper.budget.adapters.rules_file import load_rules
    from adhelper.campaigns.adapters.rules_file import load_launch_rules
    from adhelper.campaigns.domain.launch import minimum_monthly

    need = minimum_monthly(load_rules("budget-1").reserve_bp, load_launch_rules())
    assert RULES.min_monthly_rubles * 1_000_000 >= need


@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=1, max_value=50))
def test_split_budget_gives_whole_rubles_and_keeps_the_total(total: int, parts: int) -> None:
    from adhelper.budget.domain.plan import split_budget

    shares = split_budget(total, parts)
    assert len(shares) == parts
    assert sum(shares) == total
    assert max(shares) - min(shares) <= 1
    assert list(shares) == sorted(shares, reverse=True)


def test_split_budget_without_sites_is_refused() -> None:
    import pytest

    from adhelper.budget.domain.plan import split_budget

    with pytest.raises(ValueError):
        split_budget(10_000, 0)


async def test_portfolio_saves_mode_and_plans_in_one_step_and_is_isolated(
    sessions: Sessions,
    boris: uuid.UUID,  # noqa: F811
) -> None:
    from adhelper.budget.adapters.sql import SqlBudgetPortfolio

    async with sessions() as session:
        user = await session.scalar(
            __import__("sqlalchemy").text("SELECT user_id FROM businesses WHERE id = :b"),
            {"b": boris},
        )
    other = uuid.uuid4()
    await seed_account(sessions, other)
    portfolio = SqlBudgetPortfolio(sessions, user)
    assert await portfolio.get() is None
    ids = await portfolio.business_ids()
    assert ids == [boris]
    await portfolio.save_shared(30_000 * 1_000_000, {boris: 30_000 * 1_000_000}, RULES.version)
    assert await portfolio.get() == ("shared", 30_000 * 1_000_000)
    assert await SqlBudgetPlans(sessions, boris).get() == 30_000 * 1_000_000
    await portfolio.save_separate()
    assert await portfolio.get() == ("separate", None)
    # План остаётся: режим «раздельный» его не стирает.
    assert await SqlBudgetPlans(sessions, boris).get() == 30_000 * 1_000_000
    # Чужой владелец ничего не видит.
    other_user = await portfolio_user(sessions, other)
    assert await SqlBudgetPortfolio(sessions, other_user).get() is None


async def portfolio_user(sessions: Sessions, business_id: uuid.UUID) -> uuid.UUID:
    async with sessions() as session:
        user = await session.scalar(
            text("SELECT user_id FROM businesses WHERE id = :b"), {"b": business_id}
        )
    assert isinstance(user, uuid.UUID)
    return user
