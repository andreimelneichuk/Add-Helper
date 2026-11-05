"""Сценарии 9–10 и ход теста на настоящем журнале и PostgreSQL.

Варианты идут одновременно в одной группе (`experiments-2`): Яндекс сам делит показы,
сервис не дёргает паузы по дням и в конце выключает проигравшего.
"""

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
RULES = load_rules("experiments-2")
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


async def test_approved_variant_runs_beside_control_without_daily_switching(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    seen = []

    for day in range(10):
        clock.now = START + timedelta(days=day, hours=1)
        await service.tick(test_id, Observation(QUIET, QUIET, 0))
        seen.append(dict(platform.ads))

    # Оба варианта активны с одобрения и до итога: Яндекс сам делит показы.
    assert all(ads == {"ad:a": True, "ad:b": True} for ads in seen)
    changes = await SqlChangeRepository(sessions, BUSINESS).all()
    # Одно включение варианта на старте, никаких суточных пауз.
    assert [(row.source, row.kind, row.target_ref) for row in changes] == [
        (ChangeSource.EXPERIMENT, ChangeKind.RESUME_AD, "ad:b")
    ]
    assert "одновременно" in changes[0].explanation


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
    platform.ads["ad:b"] = False  # Пользователь выключил вариант в кабинете во время теста.
    before = len(await SqlChangeRepository(sessions, BUSINESS).all())

    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    record = await service.get(test_id)
    assert record.status is Status.PAUSED
    assert record.reason == "changed_outside_service"
    assert platform.ads == {"ad:a": True, "ad:b": False}
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert len(rows) == before + 1
    assert rows[-1].source is ChangeSource.EXTERNAL
    assert rows[-1].target_ref == "ad:b"
    assert rows[-1].status is ChangeStatus.APPLIED


async def test_paused_test_does_not_touch_ads_until_resumed(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    platform.ads["ad:b"] = False
    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    clock.now = START + timedelta(days=3)
    await service.tick(test_id, Observation(QUIET, QUIET, 0))
    assert platform.ads == {"ad:a": True, "ad:b": False}

    await service.resume(test_id)
    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    assert (await service.get(test_id)).status is Status.RUNNING
    assert platform.ads == {"ad:a": True, "ad:b": True}


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


async def test_winner_stays_and_loser_is_paused_through_journal_as_lost_test(
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
    assert record.explanation and "вероятностью" in record.explanation
    assert record.rules_version == "experiments-2"
    assert record.finished_at == clock.now
    assert platform.ads == {"ad:a": False, "ad:b": True}
    last = (await SqlChangeRepository(sessions, BUSINESS).all())[-1]
    assert (last.source, last.kind, last.target_ref) == (
        ChangeSource.EXPERIMENT,
        ChangeKind.PAUSE_AD,
        "ad:a",
    )
    assert last.status is ChangeStatus.APPLIED
    assert last.idempotency_key is not None
    assert last.idempotency_key.endswith(":lost_test:ad:a")
    assert "Проиграло A/B-тест" in last.explanation
    assert "вероятностью" in last.explanation
    assert await service.start("acc", "c1", "ad:b", "ad:c", Hypothesis.PRICE) > test_id


async def test_control_win_pauses_variant(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await running(service)
    clock.now = START + timedelta(days=8)

    await service.tick(
        test_id, Observation(VariantStats(400, 50, 10**9), VariantStats(400, 20, 10**9), 0)
    )

    assert (await service.get(test_id)).winner == "A"
    assert platform.ads == {"ad:a": True, "ad:b": False}


async def test_failed_switch_stays_in_journal_and_is_retried_after_read(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    clock = Clock()
    platform.faults = [Fault("set_ad_active")]
    service = build(platform, sessions, engine, clock)
    test_id = await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.OFFER)

    await service.moderation(test_id, ModerationState.APPROVED)
    assert (await service.get(test_id)).status is Status.RUNNING
    assert platform.ads["ad:b"] is False

    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    assert platform.ads == {"ad:a": True, "ad:b": True}
    assert len(await SqlChangeRepository(sessions, BUSINESS).all()) == 1


async def test_switchback_test_from_older_rules_joins_both_variants(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Тест, начатый на чередовании (`experiments-1`), переходит к одновременному показу."""
    platform = fake()
    clock = Clock()
    service = build(platform, sessions, engine, clock)
    test_id = await SqlExperimentStore(sessions, BUSINESS).add(
        "acc", "c1", "ad:a", "ad:b", "offer", "experiments-1"
    )
    store = SqlExperimentStore(sessions, BUSINESS)
    await store.set_status(test_id, Status.RUNNING, started_at=START)

    await service.tick(test_id, Observation(QUIET, QUIET, 0))

    assert platform.ads == {"ad:a": True, "ad:b": True}
    assert (await service.get(test_id)).status is Status.RUNNING


async def test_store_is_limited_to_business(sessions: Sessions, engine: AsyncEngine) -> None:
    service = build(fake(), sessions, engine, Clock())
    test_id = await service.start("acc", "c1", "ad:a", "ad:b", Hypothesis.OFFER)

    assert await SqlExperimentStore(sessions, uuid.uuid4()).get(test_id) is None


async def test_unknown_business_is_rejected_by_database(sessions: Sessions) -> None:
    with pytest.raises(IntegrityError):
        await SqlExperimentStore(sessions, uuid.uuid4()).add(
            "acc", "c1", "ad:a", "ad:b", "offer", "experiments-1"
        )
