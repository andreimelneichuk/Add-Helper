"""Суточный цикл автопилота в воркере: анализ по витрине и предложение теста владельцу."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.business.adapters.sql import SqlLeadGoal
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.domain.model import BusinessProfile, Service
from adhelper.worker.runtime import Runtime
from tests.autopilot.test_data import BUSINESS, OTHER
from tests.autopilot.test_data import _seed as _data  # noqa: F401  — витрина двух бизнесов
from tests.budget.fake_platform import FakePlatform

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
Sessions = async_sessionmaker[AsyncSession]


def runtime(sessions: Sessions, engine: AsyncEngine, connected: set[uuid.UUID]) -> Runtime:
    platform = FakePlatform([], NOW)
    return Runtime(
        sessions=sessions,
        engine=engine,
        platform_for=lambda business, _account: platform if business in connected else None,
        clock=lambda: NOW,
    )


async def test_borderline_ad_gets_test_offer_and_nothing_changes(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    # Объявление «1»: 28 дозревших дней по 100 ₽ и по обращению — цена 100 ₽ при цели 90 ₽.
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    profile = BusinessProfile("Лак", (Service("Маникюр", 1500),))
    await SqlProfiles(sessions, BUSINESS).save(profile, "")

    done = await runtime(sessions, engine, {BUSINESS}).autopilot_run()

    assert done == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None
    assert (decision.action, decision.status) == ("start_test", "awaiting_confirmation")
    assert decision.inputs["hypothesis"] == "price"
    assert decision.rules_version == "autopilot-2"
    assert await SqlDecisionLog(sessions, OTHER).latest("acc", "ad:1") is None


async def test_business_without_goal_or_platform_is_skipped(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await SqlLeadGoal(sessions, OTHER).set(90_000_000)
    assert await runtime(sessions, engine, {BUSINESS}).autopilot_run() == 0
    assert await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1") is None


async def _found_counter(sessions: Sessions, business: uuid.UUID, counter: int) -> None:
    from adhelper.business.adapters.rules_file import load_tag_rules
    from adhelper.business.adapters.tag_checks import SqlTagCheckQueue, SqlTagChecks
    from adhelper.business.application.tag import run_tag_checks
    from tests.business.test_tag import Pages

    rules = load_tag_rules()
    await SqlTagChecks(sessions, business).request("https://lak.example/", rules, NOW)
    await run_tag_checks(SqlTagCheckQueue(sessions), Pages([counter]), rules, lambda: NOW)


async def test_zero_visits_from_metrika_stop_conclusions_honestly(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    await _found_counter(sessions, BUSINESS, 98765432)
    metrika = FakePlatform([], NOW)
    metrika.visits = {NOW.date(): 0}
    worker = runtime(sessions, engine, {BUSINESS})
    worker.visits_for = lambda business: metrika if business == BUSINESS else None

    assert await worker.autopilot_run() == 1

    assert metrika.visit_calls[0][0] == "98765432"
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "acc")
    assert decision is not None
    assert (decision.verdict, decision.status) == ("untrusted", "protect_only")
    assert decision.inputs["reasons"] == ["tracking_lost"]
    # По объявлению вывода нет: при сломанном счётчике тест не предлагается.
    assert await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1") is None


async def test_metrika_without_data_keeps_cycle_as_before(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    await SqlProfiles(sessions, BUSINESS).save(
        BusinessProfile("Лак", (Service("Маникюр", 1500),)), ""
    )
    await _found_counter(sessions, BUSINESS, 98765432)
    metrika = FakePlatform([], NOW)
    worker = runtime(sessions, engine, {BUSINESS})
    worker.visits_for = lambda _business: metrika

    assert await worker.autopilot_run() == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None and decision.action == "start_test"
