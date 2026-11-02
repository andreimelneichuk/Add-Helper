"""Снятие паузы и смена бюджета на настоящем журнале: PostgreSQL и поддельная площадка."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import LedgeredBudgetPlatform, PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.emergency import StopReport, stop_account
from adhelper.budget.application.owner import (
    NOT_OWNER_PAUSE,
    BudgetStatus,
    ResumeReport,
    change_budget,
    resume_account,
)
from adhelper.budget.application.preflight import StoredPeriodPreflight
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget
from adhelper.budget.domain.watch import SpendSnapshot
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.rollback import RollbackStatus, rollback_change
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
HARD = 1_000_000
BUSINESS = uuid.uuid4()
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
async def _owners(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


async def setup(
    sessions: Sessions, engine: AsyncEngine, campaigns: list[CampaignBudget]
) -> tuple[FakePlatform, Ledger, SqlPeriodStore, int]:
    platform = FakePlatform(campaigns, NOW)
    store = SqlPeriodStore(sessions)
    period = BudgetPeriod(HARD, 1000, NOW - timedelta(days=1), NOW + timedelta(days=29))
    period_id = await store.add(BUSINESS, "acc", period, "budget-1", None, NOW)
    ledger = Ledger(
        repository=SqlChangeRepository(sessions, BUSINESS),
        gateway=PlatformChangeGateway(platform),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=StoredPeriodPreflight(
            store, platform, BUSINESS, lambda: platform.now, load_rules
        ),
    )
    return platform, ledger, store, period_id


def campaign(cid: str, limit: int, spent: int = 0) -> CampaignBudget:
    return CampaignBudget(cid, limit, spent, True, None)


async def owner_pause(platform: FakePlatform, ledger: Ledger) -> None:
    report = StopReport()
    await stop_account("acc", platform, ledger, report, "Пауза владельца", "owner:1")
    assert report.complete


async def test_owner_pause_is_lifted_through_ledger(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger, _, _ = await setup(sessions, engine, [campaign("a", 300_000)])
    await owner_pause(platform, ledger)
    report = ResumeReport()
    await resume_account("acc", platform, ledger, report, "Сняли паузу", "resume:1")

    assert report.resumed == ["acc:a"]
    assert platform.campaigns["a"].active
    rows = await SqlChangeRepository(sessions, BUSINESS).on_target("acc", "a")
    resume = rows[-1]
    assert (resume.kind, resume.source, resume.status) == (
        ChangeKind.RESUME,
        ChangeSource.USER,
        ChangeStatus.APPLIED,
    )
    assert resume.before["active"] is False
    # Снятие паузы можно отменить, как и другие действия владельца.
    assert (await rollback_change(ledger, resume.id)).status is RollbackStatus.APPLIED
    assert not platform.campaigns["a"].active


async def test_guard_stop_is_not_lifted_by_owner(sessions: Sessions, engine: AsyncEngine) -> None:
    platform, ledger, _, _ = await setup(sessions, engine, [campaign("a", 300_000)])
    guard = LedgeredBudgetPlatform(platform, ledger, ChangeSource.BUDGET_GUARD)
    await guard.pause_campaigns("acc", ["a"])
    report = ResumeReport()
    await resume_account("acc", platform, ledger, report, "Сняли паузу", "resume:1")

    assert report.refused == [("acc:a", NOT_OWNER_PAUSE)]
    assert not platform.campaigns["a"].active


async def test_resume_refused_when_spend_left_no_room(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger, _, _ = await setup(
        sessions, engine, [campaign("a", 450_000), campaign("b", 450_000)]
    )
    await owner_pause(platform, ledger)
    # Пока стояли, Яндекс досписал по статистике: остатки лимитов уже не помещаются.
    platform.campaigns["a"] = CampaignBudget("a", 450_000, 0, False, None)
    platform.campaigns["b"] = CampaignBudget("b", 450_000, 0, False, None)
    platform._history.append((platform.now, 200_000))
    report = ResumeReport()
    await resume_account("acc", platform, ledger, report, "Сняли паузу", "resume:1")

    assert report.resumed == ["acc:a"]
    assert report.refused == [("acc:b", "worst_case_exceeds_hard_limit")]
    assert not platform.campaigns["b"].active


async def test_resume_refused_while_guard_stop_is_pending(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger, store, period_id = await setup(sessions, engine, [campaign("a", 300_000)])
    await owner_pause(platform, ledger)
    await store.save_cycle(period_id, None, True, NOW)
    report = ResumeReport()
    await resume_account("acc", platform, ledger, report, "Сняли паузу", "resume:1")

    assert report.refused == [("acc:a", "budget_stop_pending")]
    assert not platform.campaigns["a"].active


async def test_resume_with_lost_answer_is_finished_by_reading(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger, _, _ = await setup(sessions, engine, [campaign("a", 300_000)])
    await owner_pause(platform, ledger)
    platform.faults.append(Fault("resume_campaigns", after_apply=True))
    report = ResumeReport()
    await resume_account("acc", platform, ledger, report, "Сняли паузу", "resume:1")
    assert report.failed == [("acc:a", "platform_unavailable")]

    await ledger.execute_pending("acc")
    rows = await SqlChangeRepository(sessions, BUSINESS).on_target("acc", "a")
    assert rows[-1].status is ChangeStatus.APPLIED
    assert [r.kind for r in rows].count(ChangeKind.RESUME) == 1


class OrderSpy(SqlPeriodStore):
    """Запоминает сумму лимитов на площадке в момент смены бюджета."""

    def __init__(self, sessions: Sessions, platform: FakePlatform) -> None:
        super().__init__(sessions)
        self.platform = platform
        self.limit_sum_at_save: int | None = None

    async def set_hard_limit(self, period_id: int, hard_limit: int) -> None:
        self.limit_sum_at_save = self.platform.limit_sum
        await super().set_hard_limit(period_id, hard_limit)


async def hard_limit(store: SqlPeriodStore) -> int:
    record = await store.active_for(BUSINESS, "acc", NOW)
    assert record is not None
    return record.period.hard_limit


async def test_lowering_budget_cuts_limits_first(sessions: Sessions, engine: AsyncEngine) -> None:
    platform, ledger, _, _ = await setup(
        sessions, engine, [campaign("a", 500_000, 100_000), campaign("b", 400_000, 100_000)]
    )
    platform._history.append((platform.now, 200_000))
    spy = OrderSpy(sessions, platform)
    result = await change_budget(spy, platform, ledger, BUSINESS, "acc", 500_000, NOW)

    assert result.status is BudgetStatus.APPLIED
    assert result.cuts == 2
    assert spy.limit_sum_at_save is not None
    assert spy.limit_sum_at_save <= BudgetPeriod(500_000, 1000).operating_budget
    assert await hard_limit(spy) == 500_000
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert {(r.kind, r.source, r.status) for r in rows} == {
        (ChangeKind.SET_LIMIT, ChangeSource.USER, ChangeStatus.APPLIED)
    }


async def test_budget_below_spent_is_refused(sessions: Sessions, engine: AsyncEngine) -> None:
    platform, ledger, store, _ = await setup(sessions, engine, [campaign("a", 500_000, 300_000)])
    platform._history.append((platform.now, 300_000))
    result = await change_budget(store, platform, ledger, BUSINESS, "acc", 250_000, NOW)

    assert result.status is BudgetStatus.BELOW_SPENT
    assert result.minimum == 333_334
    assert await hard_limit(store) == HARD
    assert platform.set_limit_calls == []


async def test_unconfirmed_cut_keeps_old_budget(sessions: Sessions, engine: AsyncEngine) -> None:
    platform, ledger, store, _ = await setup(sessions, engine, [campaign("a", 800_000)])
    platform.faults.append(Fault("set_limit"))
    result = await change_budget(store, platform, ledger, BUSINESS, "acc", 400_000, NOW)

    assert result.status is BudgetStatus.UNCONFIRMED
    assert await hard_limit(store) == HARD
    # Повтор начинается с чтения и доводит снижение до конца.
    again = await change_budget(store, platform, ledger, BUSINESS, "acc", 400_000, NOW)
    assert again.status is BudgetStatus.APPLIED
    assert platform.campaigns["a"].limit <= BudgetPeriod(400_000, 1000).operating_budget
    assert await hard_limit(store) == 400_000


async def test_raising_budget_keeps_limits(sessions: Sessions, engine: AsyncEngine) -> None:
    platform, ledger, store, _ = await setup(sessions, engine, [campaign("a", 500_000)])
    result = await change_budget(store, platform, ledger, BUSINESS, "acc", 2_000_000, NOW)

    assert (result.status, result.cuts) == (BudgetStatus.APPLIED, 0)
    assert platform.set_limit_calls == []
    assert await hard_limit(store) == 2_000_000


async def test_stale_platform_spend_does_not_hide_known_spend(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Площадка вернула статистику старше сохранённой: снижение считаем по свежей."""
    platform, ledger, store, period_id = await setup(sessions, engine, [campaign("a", 900_000)])
    await store.save_cycle(
        period_id, SpendSnapshot(600_000, NOW - timedelta(minutes=5)), False, NOW
    )
    platform._history = [(NOW - timedelta(hours=2), 0)]
    platform.stats_frozen_at = NOW - timedelta(hours=1)
    result = await change_budget(store, platform, ledger, BUSINESS, "acc", 500_000, NOW)

    assert result.status is BudgetStatus.BELOW_SPENT
    assert await hard_limit(store) == HARD
