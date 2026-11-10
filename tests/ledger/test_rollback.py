"""Откат и сценарий 8 ."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.preflight import BudgetPreflight
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget
from adhelper.budget.domain.watch import BurnRate
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.rollback import RollbackStatus, rollback_change
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from adhelper.platforms.adapters.gateway import RoutingGateway
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
OTHER = uuid.uuid4()
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
HARD = 2_000_000_000
# Повышать лимит чаще раза в неделю страж не даёт.
WEEK = timedelta(days=8)
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
async def _owners(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    await seed_account(sessions, OTHER, "acc")


def make(platform: FakePlatform, sessions: Sessions, engine: AsyncEngine) -> Ledger:
    return Ledger(
        SqlChangeRepository(sessions, BUSINESS),
        RoutingGateway(PlatformChangeGateway(platform), platform),
        PostgresAccountLock(engine),
        3,
        BudgetPreflight(
            platform,
            BudgetPeriod(HARD, 1000),
            BurnRate.zero(),
            lambda: platform.now,
            load_rules("budget-1"),
        ),
    )


def limit(campaign: str, value: int, key: str = "") -> ProposedChange:
    return ProposedChange(
        "acc",
        ChangeSource.AUTOPILOT,
        ChangeKind.SET_LIMIT,
        campaign,
        {"limit": value},
        "Лимит",
        key or f"limit:{campaign}:{value}",
    )


def pause(ref: str = "ad:1") -> ProposedChange:
    return ProposedChange(
        "acc",
        ChangeSource.AUTOPILOT,
        ChangeKind.PAUSE_AD,
        ref,
        {"active": False},
        "Пауза",
        f"pause:{ref}",
    )


async def done(ledger: Ledger, proposed: ProposedChange) -> int:
    change = await ledger.submit(proposed)
    await ledger.execute_pending("acc")
    return change.id


def platform_with(limit_value: int = 500_000_000) -> FakePlatform:
    platform = FakePlatform([CampaignBudget("c1", limit_value, 0, True, None)], NOW)
    platform.ads = {"ad:1": True}
    return platform


async def test_pause_is_reverted_by_new_record(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    original = await done(ledger, pause())

    result = await rollback_change(ledger, original)

    assert result.status is RollbackStatus.APPLIED
    assert platform.ads["ad:1"] is True
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert [row.kind for row in rows] == [ChangeKind.PAUSE_AD, ChangeKind.RESUME_AD]
    assert rows[0].status is ChangeStatus.APPLIED
    assert rows[1].source is ChangeSource.ROLLBACK
    assert rows[1].before == {"active": False, "archived": False}
    assert "нельзя вернуть" in rows[1].explanation


async def test_limit_is_returned(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    original = await done(ledger, limit("c1", 550_000_000))

    assert (await rollback_change(ledger, original)).status is RollbackStatus.APPLIED
    assert platform.campaigns["c1"].limit == 500_000_000


async def test_second_rollback_is_refused(sessions: Sessions, engine: AsyncEngine) -> None:
    ledger = make(platform_with(), sessions, engine)
    original = await done(ledger, pause())
    await rollback_change(ledger, original)

    result = await rollback_change(ledger, original)

    assert result.status is RollbackStatus.ALREADY_REVERTED
    assert len(await SqlChangeRepository(sessions, BUSINESS).all()) == 2


async def test_scenario_8_later_change_blocks_plain_rollback(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, limit("c1", 550_000_000))
    platform.now += WEEK
    second = await done(ledger, limit("c1", 600_000_000))

    result = await rollback_change(ledger, first)

    assert result.status is RollbackStatus.HAS_LATER_CHANGES
    assert [change.id for change in result.later] == [second]
    assert platform.campaigns["c1"].limit == 600_000_000


async def test_scenario_8_chain_reverts_newest_first(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, limit("c1", 550_000_000))
    platform.now += WEEK
    second = await done(ledger, limit("c1", 600_000_000))

    result = await rollback_change(ledger, first, include_later=True)

    assert result.status is RollbackStatus.APPLIED
    assert platform.campaigns["c1"].limit == 500_000_000
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert [row.reverts_change_id for row in rows[2:]] == [second, first]
    assert platform.limit_sum <= HARD


async def test_reverted_later_change_no_longer_blocks(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, limit("c1", 550_000_000))
    platform.now += WEEK
    second = await done(ledger, limit("c1", 600_000_000))
    await rollback_change(ledger, second)

    assert (await rollback_change(ledger, first)).status is RollbackStatus.APPLIED


async def test_change_made_in_cabinet_is_shown_not_overwritten(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    original = await done(ledger, limit("c1", 550_000_000))
    platform.campaigns["c1"] = CampaignBudget("c1", 700_000_000, 0, True, None)

    result = await rollback_change(ledger, original)

    assert result.status is RollbackStatus.CHANGED_OUTSIDE
    assert result.actual == {"limit": 700_000_000, "active": True}
    assert platform.campaigns["c1"].limit == 700_000_000


async def test_user_may_overwrite_change_made_in_cabinet(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    original = await done(ledger, limit("c1", 550_000_000))
    platform.campaigns["c1"] = CampaignBudget("c1", 700_000_000, 0, True, None)

    result = await rollback_change(ledger, original, overwrite_outside=True)

    assert result.status is RollbackStatus.APPLIED
    assert platform.campaigns["c1"].limit == 500_000_000


async def test_rollback_that_breaks_budget_is_refused(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Деньги уже ушли другой кампании: возврат лимита превысил бы бюджет."""
    platform = FakePlatform(
        [
            CampaignBudget("c1", 1_000_000_000, 0, True, None),
            CampaignBudget("c2", 500_000_000, 0, True, None),
        ],
        NOW,
    )
    ledger = make(platform, sessions, engine)
    original = await done(ledger, limit("c1", 900_000_000))
    platform.campaigns["c2"] = CampaignBudget("c2", 1_000_000_000, 0, True, None)

    result = await rollback_change(ledger, original)

    assert result.status is RollbackStatus.POLICY_REJECTED
    assert platform.campaigns["c1"].limit == 900_000_000
    assert platform.limit_sum <= HARD


async def test_guard_stop_cannot_be_undone_as_plain_rollback(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    ledger = make(platform_with(), sessions, engine)
    stop = ProposedChange(
        "acc",
        ChangeSource.BUDGET_GUARD,
        ChangeKind.PAUSE,
        "c1",
        {"active": False},
        "Стоп",
        "pause:c1",
    )
    original = await done(ledger, stop)

    assert (await rollback_change(ledger, original)).status is RollbackStatus.NOT_REVERSIBLE


async def test_unfinished_change_cannot_be_reverted(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    platform.faults = [Fault("set_ad_active")]
    change = await done(ledger, pause())

    assert (await rollback_change(ledger, change)).status is RollbackStatus.NOT_APPLIED


async def test_platform_failure_keeps_rollback_for_reconcile(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    original = await done(ledger, pause())
    platform.faults = [Fault("set_ad_active")]

    result = await rollback_change(ledger, original)

    assert result.status is RollbackStatus.PENDING
    await ledger.execute_pending("acc")
    assert platform.ads["ad:1"] is True
    assert (await rollback_change(ledger, original)).status is RollbackStatus.ALREADY_REVERTED


async def test_foreign_change_is_not_found(sessions: Sessions, engine: AsyncEngine) -> None:
    ledger = make(platform_with(), sessions, engine)
    original = await done(ledger, pause())
    foreign = Ledger(
        SqlChangeRepository(sessions, OTHER),
        RoutingGateway(PlatformChangeGateway(platform_with()), platform_with()),
        PostgresAccountLock(engine),
        3,
    )

    assert (await rollback_change(foreign, original)).status is RollbackStatus.NOT_FOUND


async def test_scenario_8_long_chain_reverts_strictly_newest_first(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, limit("c1", 550_000_000))
    platform.now += WEEK
    second = await done(ledger, limit("c1", 600_000_000))
    platform.now += WEEK
    third = await done(ledger, limit("c1", 650_000_000))

    result = await rollback_change(ledger, first, include_later=True)

    assert result.status is RollbackStatus.APPLIED
    assert platform.campaigns["c1"].limit == 500_000_000
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert [row.reverts_change_id for row in rows[3:]] == [third, second, first]


def archive(ref: str = "ad:1", archived: bool = True) -> ProposedChange:
    return ProposedChange(
        "acc",
        ChangeSource.USER,
        ChangeKind.ARCHIVE_AD,
        ref,
        {"archived": archived},
        "В архив",
        f"archive:{ref}:{archived}",
    )


async def test_archive_ad_goes_through_ledger_and_rolls_back(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    await done(ledger, pause())
    original = await done(ledger, archive())

    assert platform.archived["ad:1"] is True
    row = (await SqlChangeRepository(sessions, BUSINESS).all())[-1]
    assert row.kind is ChangeKind.ARCHIVE_AD and row.status is ChangeStatus.APPLIED
    assert row.before == {"active": False, "archived": False}

    result = await rollback_change(ledger, original)

    assert result.status is RollbackStatus.APPLIED
    assert platform.archived["ad:1"] is False
