"""Смена района и часов показа на настоящем журнале: PostgreSQL и поддельная площадка."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.domain.limits import CampaignBudget
from adhelper.campaigns.adapters.rules_file import load_targeting_rules
from adhelper.campaigns.application.targeting import (
    TARGETING_PREFIX,
    TargetingStatus,
    change_regions,
    change_schedule,
)
from adhelper.campaigns.domain.targeting import Schedule
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.rollback import RollbackStatus, rollback_change
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus
from adhelper.platforms.adapters.gateway import RoutingGateway
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
BUSINESS = uuid.uuid4()
RULES = load_targeting_rules("targeting-1")
WORK = Schedule((0, 1, 2, 3, 4, 5), 10, 21)
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
async def _owners(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


def setup(sessions: Sessions, engine: AsyncEngine) -> tuple[FakePlatform, Ledger]:
    platform = FakePlatform(
        [CampaignBudget("a", 300_000, 0, True, None), CampaignBudget("b", 300_000, 0, True, None)],
        NOW,
    )
    ledger = Ledger(
        repository=SqlChangeRepository(sessions, BUSINESS),
        gateway=RoutingGateway(PlatformChangeGateway(platform), platform, platform),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
    )
    return platform, ledger


async def test_schedule_goes_through_ledger_and_can_be_undone(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger = setup(sessions, engine)
    result = await change_schedule(ledger, "acc", ["a", "b"], WORK, RULES, "req-1")

    assert result.status is TargetingStatus.APPLIED
    assert result.applied == ["a", "b"]
    assert platform.targeting["a"].schedule == WORK
    rows = await SqlChangeRepository(sessions, BUSINESS).on_target("acc", f"{TARGETING_PREFIX}a")
    change = rows[-1]
    assert (change.kind, change.source, change.status) == (
        ChangeKind.SET_SCHEDULE,
        ChangeSource.USER,
        ChangeStatus.APPLIED,
    )
    assert change.before["schedule"] == {"days": [0, 1, 2, 3, 4, 5, 6], "hours": [0, 24]}
    assert change.planned == {"schedule": {"days": [0, 1, 2, 3, 4, 5], "hours": [10, 21]}}
    assert "пн–сб, 10:00–21:00" in change.explanation

    assert (await rollback_change(ledger, change.id)).status is RollbackStatus.APPLIED
    assert platform.targeting["a"].schedule == Schedule((0, 1, 2, 3, 4, 5, 6), 0, 24)


async def test_bad_schedule_is_refused_before_ledger(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger = setup(sessions, engine)
    result = await change_schedule(ledger, "acc", ["a"], Schedule((6,), 10, 17), RULES, "req-1")

    assert result.status is TargetingStatus.REFUSED
    assert result.problem == "too_few_hours"
    assert await SqlChangeRepository(sessions, BUSINESS).all() == []
    assert platform.targeting == {}


async def test_regions_change_and_unknown_region_refused(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger = setup(sessions, engine)
    refused = await change_regions(ledger, "acc", ["a"], (213, 999_999), RULES, "req-1")
    assert (refused.status, refused.problem) == (TargetingStatus.REFUSED, "unknown_region")

    result = await change_regions(ledger, "acc", ["a", "b"], (2, 213, 2), RULES, "req-2")
    assert result.status is TargetingStatus.APPLIED
    assert platform.targeting["b"].regions == (2, 213)
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert {r.kind for r in rows} == {ChangeKind.SET_REGIONS}
    assert all(r.planned == {"regions": [2, 213]} for r in rows)


async def test_lost_answer_is_finished_by_reading_and_repeat_adds_nothing(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger = setup(sessions, engine)
    platform.faults.append(Fault("set_schedule", after_apply=True))
    first = await change_schedule(ledger, "acc", ["a"], WORK, RULES, "req-1")
    assert first.status is TargetingStatus.UNCONFIRMED
    assert first.failed == ["a"]

    # Повтор того же запроса начинается с чтения: на площадке уже стоит нужное.
    again = await change_schedule(ledger, "acc", ["a"], WORK, RULES, "req-1")
    assert again.status is TargetingStatus.APPLIED
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert len(rows) == 1
    assert rows[0].status is ChangeStatus.APPLIED
    assert platform.schedule_calls == 1


async def test_failed_apply_holds_queue_and_repeat_finishes(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform, ledger = setup(sessions, engine)
    platform.faults.append(Fault("set_regions"))
    result = await change_regions(ledger, "acc", ["a", "b"], (2,), RULES, "req-1")

    # Очередь кабинета стоит на первой несделанной записи, вторая ждёт её.
    assert result.status is TargetingStatus.UNCONFIRMED
    assert (result.applied, result.failed) == ([], ["a", "b"])
    assert platform.targeting == {}

    again = await change_regions(ledger, "acc", ["a", "b"], (2,), RULES, "req-1")
    assert (again.status, again.applied) == (TargetingStatus.APPLIED, ["a", "b"])
    assert len(await SqlChangeRepository(sessions, BUSINESS).all()) == 2


async def test_platform_down_changes_nothing(sessions: Sessions, engine: AsyncEngine) -> None:
    platform, ledger = setup(sessions, engine)
    platform.unavailable = True
    result = await change_schedule(ledger, "acc", ["a"], WORK, RULES, "req-1")

    assert (result.status, result.failed) == (TargetingStatus.UNCONFIRMED, ["a"])
    assert await SqlChangeRepository(sessions, BUSINESS).all() == []


async def test_no_campaigns_means_nothing_to_change(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    _, ledger = setup(sessions, engine)
    result = await change_schedule(ledger, "acc", [], WORK, RULES, "req-1")
    assert result.status is TargetingStatus.NO_CAMPAIGNS
