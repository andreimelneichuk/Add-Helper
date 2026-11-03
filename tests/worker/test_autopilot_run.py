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
