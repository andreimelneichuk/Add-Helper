"""Сценарии 9–10 и ход теста на настоящем журнале и PostgreSQL."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.experiments.adapters.ledgered import LedgeredSwitch
from adhelper.experiments.adapters.rules_file import load_rules
from adhelper.experiments.adapters.sql import SqlExperimentStore
from adhelper.experiments.application.service import (
    ExperimentService,
    Hypothesis,
    ModerationState,
    Observation,
    TestAlreadyRunning,
)
from adhelper.experiments.domain.model import Status, VariantStats
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus
from adhelper.platforms.adapters.gateway import RoutingGateway
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RULES = load_rules("experiments-1")
Sessions = async_sessionmaker[AsyncSession]
QUIET = VariantStats(0, 0, 0)


@pytest.fixture(autouse=True)
async def _owners(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def build(
    platform: FakePlatform, sessions: Sessions, engine: AsyncEngine, clock: Clock
) -> ExperimentService:
    ledger = Ledger(
        SqlChangeRepository(sessions, BUSINESS),
        RoutingGateway(PlatformChangeGateway(platform), platform),
        PostgresAccountLock(engine),
        3,
    )
    return ExperimentService(
        SqlExperimentStore(sessions, BUSINESS), LedgeredSwitch(ledger), platform, clock, RULES
    )


def fake() -> FakePlatform:
    platform = FakePlatform([], START)
    platform.ads = {"ad:a": True, "ad:b": False}
    return platform


async def running(service: ExperimentService) -> int:
    test_id = await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.OFFER)
    await service.moderation(test_id, ModerationState.APPROVED)
    return test_id


async def test_only_one_test_per_campaign(sessions: Sessions, engine: AsyncEngine) -> None:
    service = build(fake(), sessions, engine, Clock())
    await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.OFFER)

    with pytest.raises(TestAlreadyRunning):
        await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.PRICE)
    assert await service.start("acc", "c2", "ad:c", "ad:d", Hypothesis.PRICE) > 0


async def test_scenario_10_moderation_refusal_cancels_and_frees_campaign(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    service = build(platform, sessions, engine, Clock())
    test_id = await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.OFFER)

    await service.moderation(test_id, ModerationState.REJECTED)

    record = await service.get(test_id)
    assert record.status is Status.CANCELLED
    assert record.reason == "moderation_rejected"
    assert platform.ads == {"ad:a": True, "ad:b": False}
    assert await SqlChangeRepository(sessions, BUSINESS).all() == []
    assert await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.PRICE) > test_id


async def test_switching_follows_schedule_and_never_empties_group(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    seen = []

    for day in range(4):
        clock.now = START + timedelta(days=day)
        await service.tick(test_id, Observation(QUIET, QUIET, 0))
        seen.append(dict(platform.ads))

    assert [ads["ad:b"] for ads in seen] == [False, True, True, False]
    assert all(any(ads.values()) for ads in seen)
    changes = await SqlChangeRepository(sessions, BUSINESS).all()
    assert all(row.source is ChangeSource.EXPERIMENT for row in changes)
    # Сначала включаем новый вариант, чтобы группа не оставалась без показов.
    assert [row.kind for row in changes[:2]] == [ChangeKind.RESUME_AD, ChangeKind.PAUSE_AD]


async def test_repeated_tick_does_not_add_changes(sessions: Sessions, engine: AsyncEngine) -> None:
    clock = Clock()
    service = build(fake(), sessions, engine, clock)
    test_id = await running(service)
    clock.now = START + timedelta(days=1)

    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    count = len(await SqlChangeRepository(sessions, BUSINESS).all())
    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    assert len(await SqlChangeRepository(sessions, BUSINESS).all()) == count


async def test_scenario_9_change_in_cabinet_pauses_test_and_is_journaled(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    platform.ads["ad:b"] = True  # Пользователь включил вариант в кабинете в день варианта A.
    before = len(await SqlChangeRepository(sessions, BUSINESS).all())

    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    record = await service.get(test_id)
    assert record.status is Status.PAUSED
    assert record.reason == "changed_outside_service"
    assert platform.ads == {"ad:a": True, "ad:b": True}
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert len(rows) == before + 1
    assert rows[-1].source is ChangeSource.EXTERNAL
    assert rows[-1].target_ref == "ad:b"
    assert rows[-1].status is ChangeStatus.APPLIED


async def test_paused_test_does_not_switch_until_resumed(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    platform.ads["ad:b"] = True
    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    clock.now = START + timedelta(days=3)
    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    assert platform.ads == {"ad:a": True, "ad:b": True}

    await service.resume(test_id)
    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    assert (await service.get(test_id)).status is Status.RUNNING
    assert platform.ads == {"ad:a": True, "ad:b": False}


async def test_bad_data_pauses_and_recovery_resumes(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    clock = Clock()
    service = build(fake(), sessions, engine, clock)
    test_id = await running(service)

    await service.tick(test_id, Observation(QUIET, QUIET, 0, data_trusted=False))
    record = await service.get(test_id)
    assert (record.status, record.reason) == (Status.PAUSED, "data_untrusted")

    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    assert (await service.get(test_id)).status is Status.RUNNING


async def test_finished_test_always_applies_decision(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    clock.now = START + timedelta(days=8)

    await service.tick(
        test_id, Observation(VariantStats(400, 20, 10**9), VariantStats(400, 50, 10**9), 0)
    )

    record = await service.get(test_id)
    assert record.status is Status.APPLIED
    assert record.winner == "B"
    assert record.explanation
    assert record.rules_version == "experiments-1"
    assert platform.ads == {"ad:a": False, "ad:b": True}
    assert await service.start("acc", "c1", "ad:b", "ad:c", Hypothesis.PRICE) > test_id


async def test_failed_switch_stays_in_journal_and_is_retried_after_read(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    clock.now = START + timedelta(days=1)
    platform.faults = [Fault("set_ad_active")]

    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    assert (await service.get(test_id)).status is Status.RUNNING

    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    assert platform.ads == {"ad:a": False, "ad:b": True}


async def test_store_is_limited_to_business(sessions: Sessions, engine: AsyncEngine) -> None:
    service = build(fake(), sessions, engine, Clock())
    test_id = await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.OFFER)

    assert await SqlExperimentStore(sessions, uuid.uuid4()).get(test_id) is None


async def test_unknown_business_is_rejected_by_database(sessions: Sessions) -> None:
    with pytest.raises(IntegrityError):
        await SqlExperimentStore(sessions, uuid.uuid4()).add(
            "acc", "c1", "ad:a", "ad:b", "offer", "experiments-1"
        )
