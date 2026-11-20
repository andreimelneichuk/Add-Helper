"""Отчёты без целей Метрики: считаем по всем, говорим владельцу, деньги не трогаем."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.domain.ports import GoalsUnavailable
from adhelper.notifications.adapters.sql import SqlAlerts
from adhelper.notifications.domain.model import METRIKA_GOALS_ALERT
from adhelper.worker import runtime as runtime_module
from adhelper.worker.runtime import Runtime
from tests.seed import seed_account

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
BUSINESS = uuid.uuid4()
Sessions = async_sessionmaker[AsyncSession]


class StatsPlatform:
    """Площадка, у которой Метрика отвечает по целям или отказывает."""

    def __init__(self, goals_ok: bool) -> None:
        self.goals_ok = goals_ok
        self.skip_goals = False
        self.calls = 0

    async def managed_campaign_ids(self, account: str) -> list[str]:
        return ["1"]

    async def inventory(self, account: str, ids: Any) -> tuple[list[Any], list[Any]]:
        return [], []

    async def campaign_days(self, account: str, ids: Any, since: Any, until: Any) -> list[Any]:
        self.calls += 1
        if not self.goals_ok and not self.skip_goals:
            raise GoalsUnavailable("metrika goals unavailable")
        return []

    async def ad_days(self, account: str, ids: Any, since: Any, until: Any) -> list[Any]:
        return []


def runtime(sessions: Sessions, engine: AsyncEngine, platform: StatsPlatform) -> Runtime:
    return Runtime(
        sessions=sessions, engine=engine, platform_for=lambda _b, _a: platform, clock=lambda: NOW
    )


@pytest.fixture(autouse=True)
async def _account(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


@pytest.fixture(autouse=True)
def _reports(monkeypatch: pytest.MonkeyPatch) -> None:
    async def report_accounts(_sessions: Sessions) -> list[tuple[uuid.UUID, str]]:
        return [(BUSINESS, "acc")]

    monkeypatch.setattr(runtime_module, "report_accounts", report_accounts)


async def test_goals_denied_falls_back_to_all_goals_and_tells_the_owner(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = StatsPlatform(goals_ok=False)

    await runtime(sessions, engine, platform).stats_sync()

    # Первая попытка — с целями, вторая — по всем целям.
    assert platform.calls == 2
    assert platform.skip_goals is True
    assert METRIKA_GOALS_ALERT in await SqlAlerts(sessions, BUSINESS).open_kinds()


async def test_restored_access_closes_the_alert(sessions: Sessions, engine: AsyncEngine) -> None:
    broken = StatsPlatform(goals_ok=False)
    await runtime(sessions, engine, broken).stats_sync()
    assert METRIKA_GOALS_ALERT in await SqlAlerts(sessions, BUSINESS).open_kinds()

    fixed = StatsPlatform(goals_ok=True)
    await runtime(sessions, engine, fixed).stats_sync()

    assert fixed.calls == 1
    assert METRIKA_GOALS_ALERT not in await SqlAlerts(sessions, BUSINESS).open_kinds()
