"""Фоновые циклы на настоящем PostgreSQL и поддельной площадке."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.watch_cycle import run_watch_cycle
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget
from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.ledger.adapters.sql import SqlChangeRepository, accounts_with_open_changes
from adhelper.ledger.domain.model import ChangeKind, ChangeStatus
from adhelper.notifications.adapters.sql import SqlAlerts
from adhelper.worker.runtime import Runtime
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
HARD = 1_000_000_000
BUSINESS = uuid.uuid4()
OTHER = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _owners(sessions: async_sessionmaker[AsyncSession]) -> None:
    await seed_account(sessions, BUSINESS, "acc", "bad", "good", "other")
    await seed_account(sessions, OTHER, "acc", "bad", "good", "other")


Sessions = async_sessionmaker[AsyncSession]


def campaign(limit: int = 2_000_000_000) -> CampaignBudget:
    return CampaignBudget("a", limit, 0, True, None)


async def add_period(
    store: SqlPeriodStore, business: uuid.UUID = BUSINESS, account: str = "acc"
) -> int:
    return await store.add(
        business,
        account,
        BudgetPeriod(HARD, 1000, NOW - timedelta(days=1), NOW + timedelta(days=29)),
        "budget-1",
        opening_balance=None,
        next_check_at=NOW,
    )


def runtime(
    sessions: Sessions, engine: AsyncEngine, platforms: dict[tuple[uuid.UUID, str], FakePlatform]
) -> Runtime:
    return Runtime(
        sessions=sessions,
        engine=engine,
        platform_for=lambda business, account: platforms.get((business, account)),
        clock=lambda: next(iter(platforms.values())).now,
    )


async def test_watch_stops_through_journal_and_raises_alert(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = FakePlatform([campaign()], NOW, enforces_limits=False)
    platform.run(minutes=1, per_minute=980_000_000)
    store = SqlPeriodStore(sessions)
    await add_period(store)
    rt = runtime(sessions, engine, {(BUSINESS, "acc"): platform})

    await rt.budget_watch()

    assert not platform.campaigns["a"].active
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert [(r.kind, r.status) for r in rows] == [(ChangeKind.PAUSE, ChangeStatus.APPLIED)]
    assert await SqlAlerts(sessions, BUSINESS).open_kinds() == ["budget_stopped"]


async def test_period_is_skipped_until_next_check(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = FakePlatform([campaign()], NOW)
    store = SqlPeriodStore(sessions)
    await add_period(store)
    rt = runtime(sessions, engine, {(BUSINESS, "acc"): platform})

    await rt.budget_watch()
    assert await store.due(platform.now) == []

    platform.now += timedelta(seconds=899)
    assert await store.due(platform.now) == []
    platform.now += timedelta(seconds=1)
    assert len(await store.due(platform.now)) == 1


async def test_burn_survives_restart_when_stats_freeze(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Статистика зависла, сервис перезапущен: скорость расхода берётся из базы."""
    platform = FakePlatform([campaign()], NOW, enforces_limits=False)
    store = SqlPeriodStore(sessions)
    await add_period(store)
    rt = runtime(sessions, engine, {(BUSINESS, "acc"): platform})
    for _ in range(4):
        platform.run(minutes=15, per_minute=10_000_000)
        platform.now += timedelta(seconds=1)
        await rt.budget_watch()
    frozen_at = platform.now
    assert platform.campaigns["a"].active

    # Расход продолжается, но отчёт больше не обновляется.
    platform.stats_frozen_at = frozen_at
    platform.now = frozen_at + timedelta(hours=10)

    await runtime(sessions, engine, {(BUSINESS, "acc"): platform}).budget_watch()

    assert not platform.campaigns["a"].active


async def test_platform_down_keeps_retrying_and_raises_alert(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = FakePlatform([campaign()], NOW)
    platform.unavailable = True
    store = SqlPeriodStore(sessions)
    await add_period(store)
    rt = runtime(sessions, engine, {(BUSINESS, "acc"): platform})

    await rt.budget_watch()

    alerts = SqlAlerts(sessions, BUSINESS)
    assert await alerts.open_kinds() == ["platform_unavailable"]
    platform.now += timedelta(seconds=300)
    assert len(await store.due(platform.now)) == 1

    platform.unavailable = False
    await rt.budget_watch()
    assert await alerts.open_kinds() == []


async def test_stop_pending_survives_restart(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = FakePlatform([campaign()], NOW, enforces_limits=False)
    platform.run(minutes=1, per_minute=980_000_000)
    platform.faults.append(Fault("pause_campaigns"))
    store = SqlPeriodStore(sessions)
    await add_period(store)

    await runtime(sessions, engine, {(BUSINESS, "acc"): platform}).budget_watch()
    assert platform.campaigns["a"].active
    assert "budget_stop_pending" in await SqlAlerts(sessions, BUSINESS).open_kinds()

    platform.now += timedelta(seconds=300)
    await runtime(sessions, engine, {(BUSINESS, "acc"): platform}).budget_watch()

    assert not platform.campaigns["a"].active


async def test_broken_business_does_not_block_others(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    class Broken(FakePlatform):
        async def fetch_spend(self, account: str):  # type: ignore[no-untyped-def]
            raise RuntimeError("секрет не должен попасть в лог")

    broken = Broken([campaign()], NOW)
    healthy = FakePlatform([campaign()], NOW, enforces_limits=False)
    healthy.run(minutes=1, per_minute=980_000_000)
    broken.now = healthy.now
    store = SqlPeriodStore(sessions)
    await add_period(store, BUSINESS, "bad")
    await add_period(store, OTHER, "good")

    await runtime(
        sessions, engine, {(BUSINESS, "bad"): broken, (OTHER, "good"): healthy}
    ).budget_watch()

    assert not healthy.campaigns["a"].active


async def test_no_platform_is_skipped_without_failure(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    store = SqlPeriodStore(sessions)
    await add_period(store)
    fake = FakePlatform([campaign()], NOW)

    await runtime(sessions, engine, {(OTHER, "x"): fake}).budget_watch()

    assert await SqlAlerts(sessions, BUSINESS).open_kinds() == ["platform_not_connected"]


async def test_reconcile_finishes_change_left_by_crash(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = FakePlatform([CampaignBudget("a", 300_000_000, 0, True, None)], NOW)
    rt = runtime(sessions, engine, {(BUSINESS, "acc"): platform})
    store = SqlPeriodStore(sessions)
    await add_period(store)
    await rt.ledger_for(BUSINESS, "acc").submit(rt.limit_proposal("acc", "a", 200_000_000))
    assert await accounts_with_open_changes(sessions) == [(BUSINESS, "acc")]

    await rt.state_reconcile()

    assert platform.campaigns["a"].limit == 200_000_000
    assert await accounts_with_open_changes(sessions) == []


async def test_reconcile_without_period_refuses_increase_but_allows_decrease(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Периода нет — проверить бюджет не с чем, поэтому рост запрещён, а снижение безопасно."""
    platform = FakePlatform(
        [
            CampaignBudget("a", 300_000_000, 0, True, None),
            CampaignBudget("b", 300_000_000, 0, True, None),
        ],
        NOW,
    )
    rt = runtime(sessions, engine, {(BUSINESS, "acc"): platform})
    ledger = rt.ledger_for(BUSINESS, "acc")
    await ledger.submit(rt.limit_proposal("acc", "a", 360_000_000))
    await ledger.submit(rt.limit_proposal("acc", "b", 200_000_000))

    await rt.state_reconcile()

    assert platform.campaigns["a"].limit == 300_000_000
    assert platform.campaigns["b"].limit == 200_000_000
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert rows[0].failure_reason == "no_active_period"


async def test_cycle_function_uses_injected_store_only(sessions: Sessions) -> None:
    processed = await run_watch_cycle(
        SqlPeriodStore(sessions), lambda *_: None, SqlAlerts.factory(sessions), NOW, load_rules
    )

    assert processed == 0


def test_platform_unavailable_is_public_error() -> None:
    assert issubclass(PlatformUnavailable, Exception)
