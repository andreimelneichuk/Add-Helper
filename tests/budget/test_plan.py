"""План бюджета до запуска: сумма из мастера, пределы из правил, хранится в сервисе."""

import uuid

from hypothesis import given
from hypothesis import strategies as st

from adhelper.budget.adapters.plan_rules import load_plan_rules
from adhelper.budget.adapters.sql import SqlBudgetPlans
from adhelper.budget.domain.plan import PlanProblem, check_plan
from tests.business.test_site_reads_sql import Sessions, boris  # noqa: F401
from tests.seed import seed_account

RULES = load_plan_rules()


def test_rules_are_sane() -> None:
    assert RULES.version == "plan-1"
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
