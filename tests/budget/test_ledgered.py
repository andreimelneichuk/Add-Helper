"""Страж бюджета на настоящем журнале: PostgreSQL, сбои и поддельная площадка."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import LedgeredBudgetPlatform, PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.guard import (
    ChangeStatus as GuardStatus,
)
from adhelper.budget.application.guard import (
    TransferStatus,
    WatchState,
    apply_limit_change,
    drive_transfer,
    watch_once,
)
from adhelper.budget.application.preflight import BudgetPreflight
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, LimitChange
from adhelper.budget.domain.transfer import plan_transfer
from adhelper.budget.domain.watch import BurnRate, WatchAction
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RULES = replace(
    load_rules("budget-1"),
    max_increase_bp=1_000_000,
    min_increase_interval_days=0,
    change_spike_old_bp=0,
    change_spike_new_bp=0,
)
PERIOD = BudgetPeriod(hard_limit=1_000_000, reserve_bp=1000)
ZERO = BurnRate.zero()
BUSINESS = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _owners(sessions: async_sessionmaker[AsyncSession]) -> None:
    await seed_account(sessions, BUSINESS, "acc")


Sessions = async_sessionmaker[AsyncSession]


def campaign(campaign_id: str, limit: int) -> CampaignBudget:
    return CampaignBudget(campaign_id, limit, 0, True, None)


def build(
    platform: FakePlatform, sessions: Sessions, engine: AsyncEngine
) -> tuple[LedgeredBudgetPlatform, Ledger, SqlChangeRepository]:
    repository = SqlChangeRepository(sessions, BUSINESS)
    ledger = Ledger(
        repository=repository,
        gateway=PlatformChangeGateway(platform),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=BudgetPreflight(platform, PERIOD, ZERO, lambda: platform.now, RULES),
    )
    return LedgeredBudgetPlatform(platform, ledger, ChangeSource.AUTOPILOT), ledger, repository


async def test_transfer_with_lost_response_is_journaled_once(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    start = [campaign("a", 500_000), campaign("b", 100_000)]
    platform = FakePlatform(start, NOW)
    platform.faults.append(Fault("set_limit", after_apply=True))
    ledgered, _, repository = build(platform, sessions, engine)
    plan = plan_transfer(start[0], start[1], 50_000)

    status = await drive_transfer(ledgered, "acc", PERIOD, plan, ZERO, NOW, RULES)

    # Журнал сам завершает шаг с потерянным ответом по чтению факта.
    assert status is TransferStatus.DONE
    assert platform.set_limit_calls == [LimitChange("a", 450_000), LimitChange("b", 150_000)]
    assert max(platform.limit_sums) == 600_000
    rows = await repository.all()
    assert [(r.target_ref, r.status) for r in rows] == [
        ("a", ChangeStatus.APPLIED),
        ("b", ChangeStatus.APPLIED),
    ]
    assert rows[0].before["limit"] == 500_000
    assert rows[0].after is not None
    assert rows[0].after["limit"] == 450_000


async def test_stuck_increase_and_new_increase_cannot_break_operating_budget(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Рост застрял в журнале после сбоя. Новая проверка его не видит, но применение защищено."""
    platform = FakePlatform([campaign("a", 400_000), campaign("b", 400_000)], NOW)
    ledgered, ledger, repository = build(platform, sessions, engine)
    await ledger.submit(
        ProposedChange(
            account="acc",
            source=ChangeSource.AUTOPILOT,
            kind=ChangeKind.SET_LIMIT,
            target_ref="a",
            planned={"limit": 480_000},
            explanation="Рост, разрешённый до сбоя",
            idempotency_key="stuck",
        )
    )

    await apply_limit_change(ledgered, "acc", PERIOD, LimitChange("b", 490_000), ZERO, NOW, RULES)

    assert max(platform.limit_sums) <= PERIOD.operating_budget
    assert platform.limit_sum == 880_000
    rows = await repository.all()
    assert [(r.target_ref, r.status) for r in rows] == [
        ("a", ChangeStatus.APPLIED),
        ("b", ChangeStatus.FAILED),
    ]


async def test_rejected_change_is_journaled_and_not_applied(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = FakePlatform([campaign("a", 500_000)], NOW)
    ledgered, _, repository = build(platform, sessions, engine)

    outcome = await apply_limit_change(
        ledgered, "acc", PERIOD, LimitChange("a", 900_001), ZERO, NOW, RULES
    )

    assert outcome.status is GuardStatus.REJECTED
    assert platform.set_limit_calls == []
    row = (await repository.all())[0]
    assert row.status is ChangeStatus.REJECTED
    assert row.failure_reason == "exceeds_operating_budget"


async def test_preflight_refuses_increase_that_became_unsafe(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = FakePlatform([campaign("a", 400_000), campaign("b", 400_000)], NOW)
    _, ledger, repository = build(platform, sessions, engine)
    await ledger.submit(
        ProposedChange(
            account="acc",
            source=ChangeSource.AUTOPILOT,
            kind=ChangeKind.SET_LIMIT,
            target_ref="a",
            planned={"limit": 480_000},
            explanation="Рост, разрешённый до сбоя",
            idempotency_key="stale",
        )
    )
    platform.campaigns["b"] = replace(platform.campaigns["b"], limit=490_000)

    await ledger.execute_pending("acc")

    assert platform.limit_sum == 890_000
    assert platform.set_limit_calls == []
    row = (await repository.all())[0]
    assert row.status is ChangeStatus.FAILED
    assert row.failure_reason == "exceeds_operating_budget"


async def test_watch_stop_goes_through_journal(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = FakePlatform([campaign("a", 2_000_000)], NOW, enforces_limits=False)
    platform.run(minutes=1, per_minute=980_000)
    ledgered, _, repository = build(platform, sessions, engine)

    result = await watch_once(ledgered, "acc", PERIOD, WatchState.initial(), platform.now, RULES)

    assert result.stopped
    rows = await repository.all()
    assert [(r.kind, r.status) for r in rows] == [(ChangeKind.PAUSE, ChangeStatus.APPLIED)]
    assert rows[0].before["active"] is True


async def test_ledgered_platform_enriches_last_increase_at_and_rejects_too_soon(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rules_with_cooldown = replace(RULES, min_increase_interval_days=7)
    platform = FakePlatform([campaign("a", 100_000)], NOW)
    ledgered, _, _ = build(platform, sessions, engine)

    now = datetime.now(UTC)
    outcome1 = await apply_limit_change(
        ledgered, "acc", PERIOD, LimitChange("a", 110_000), ZERO, now, rules_with_cooldown
    )
    assert outcome1.status is GuardStatus.APPLIED

    campaigns = await ledgered.read_campaigns("acc")
    assert campaigns[0].last_increase_at is not None

    outcome2 = await apply_limit_change(
        ledgered,
        "acc",
        PERIOD,
        LimitChange("a", 120_000),
        ZERO,
        now + timedelta(days=1),
        rules_with_cooldown,
    )
    assert outcome2.status is GuardStatus.REJECTED
    assert outcome2.decision is not None
    assert outcome2.decision.reason.value == "too_soon"


async def test_watch_does_not_stop_when_account_has_historical_spend_with_opening_spent(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = FakePlatform([campaign("a", 500_000)], NOW, enforces_limits=False)
    platform.run(minutes=1, per_minute=5_000_000)
    ledgered, _, _ = build(platform, sessions, engine)

    result = await watch_once(
        ledgered,
        "acc",
        PERIOD,
        WatchState.initial(),
        platform.now,
        RULES,
        opening_spent=5_000_000,
        opening_balance=100_000,
    )
    assert not result.stopped
    assert result.verdict is not None
    assert result.verdict.action is WatchAction.OK
