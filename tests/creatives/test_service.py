"""Генерация в сервисе: очередь наборов в настоящем PostgreSQL, модель поддельная."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.adapters.sql import (
    SqlCreativeQueue,
    SqlCreativeSets,
    SqlProfiles,
    SqlProfileSource,
)
from adhelper.creatives.application.service import (
    CURRENT_RULES,
    MAX_ATTEMPTS,
    STALE_AFTER,
    TooManySets,
    request_set,
    run_queued,
)
from adhelper.creatives.domain.model import (
    BusinessProfile,
    CreativeSource,
    DraftDecision,
    ModelReply,
    Service,
    SetStatus,
)
from tests.seed import seed_account

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
RULES = load_rules(CURRENT_RULES)
PROFILE = BusinessProfile(
    "Тест", (Service("Маникюр", 1500),), "ул. Ленина, 5", ("Стерильные инструменты",), "маникюр"
)
GOOD = {"headline": "Маникюр за 1500 ₽", "text": "Стерильные инструменты, ул. Ленина, 5."}
INVENTED = {"headline": "Маникюр за 500 ₽", "text": "Только сегодня скидка!"}


class FakeModel:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls = 0

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return ModelReply(self.reply, 100)


def ads(*items: dict[str, str]) -> str:
    return json.dumps({"ads": list(items)}, ensure_ascii=False)


@pytest.fixture
async def business(sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id)
    await SqlProfiles(sessions, business_id).save(PROFILE, "https://lak.example")
    return business_id


async def _run(
    sessions: async_sessionmaker[AsyncSession], model: FakeModel | None, now: datetime = NOW
) -> int:
    return await run_queued(
        SqlCreativeQueue(sessions), SqlProfileSource(sessions), model, None, load_rules, lambda: now
    )


async def test_profile_round_trip_takes_name_from_business(
    sessions: async_sessionmaker[AsyncSession], business: uuid.UUID
) -> None:
    assert await SqlProfiles(sessions, business).get() == (PROFILE, "https://lak.example")
    assert await SqlProfiles(sessions, uuid.uuid4()).get() is None


async def test_queued_set_is_generated_checked_and_stored(
    sessions: async_sessionmaker[AsyncSession], business: uuid.UUID
) -> None:
    sets = SqlCreativeSets(sessions, business)
    set_id = await request_set(sets, RULES, NOW)
    assert (await sets.latest(1))[0].status is SetStatus.QUEUED

    assert await _run(sessions, FakeModel(ads(GOOD, INVENTED))) == 1

    stored = (await sets.latest(1))[0]
    assert stored.id == set_id
    assert stored.status is SetStatus.READY
    assert stored.source is CreativeSource.MODEL
    assert stored.rules_version == CURRENT_RULES
    assert [(d.headline, d.decision) for d in stored.drafts] == [
        (GOOD["headline"], DraftDecision.PROPOSED)
    ]
    assert stored.rejected[0].headline == INVENTED["headline"]
    assert "unconfirmed_number" in stored.rejected[0].reasons
    assert stored.tokens_used == 100
    assert stored.finished_at == NOW
    # Готовый набор второй раз не генерируется.
    assert await _run(sessions, FakeModel(ads(GOOD))) == 0


async def test_daily_limit_counts_sliding_day(
    sessions: async_sessionmaker[AsyncSession], business: uuid.UUID
) -> None:
    sets = SqlCreativeSets(sessions, business)
    for minute in range(RULES.max_sets_per_day):
        await request_set(sets, RULES, NOW + timedelta(minutes=minute))
    with pytest.raises(TooManySets):
        await request_set(sets, RULES, NOW + timedelta(hours=1))
    # Сутки прошли от последнего набора: окно скользящее, а не календарное.
    await request_set(sets, RULES, NOW + timedelta(hours=24, minutes=RULES.max_sets_per_day))


async def test_sets_and_decisions_are_isolated_by_business(
    sessions: async_sessionmaker[AsyncSession], business: uuid.UUID
) -> None:
    other = uuid.uuid4()
    await seed_account(sessions, other)
    set_id = await request_set(SqlCreativeSets(sessions, business), RULES, NOW)
    await _run(sessions, FakeModel(ads(GOOD)))

    assert await SqlCreativeSets(sessions, other).latest(10) == []
    assert not await SqlCreativeSets(sessions, other).decide(set_id, 0, DraftDecision.APPROVED)
    sets = SqlCreativeSets(sessions, business)
    assert not await sets.decide(set_id, 5, DraftDecision.APPROVED)
    assert await sets.decide(set_id, 0, DraftDecision.APPROVED)
    assert (await sets.latest(1))[0].drafts[0].decision is DraftDecision.APPROVED


async def test_without_profile_set_fails_without_model_call(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id)
    sets = SqlCreativeSets(sessions, business_id)
    await request_set(sets, RULES, NOW)
    model = FakeModel(ads(GOOD))

    assert await _run(sessions, model) == 0

    stored = (await sets.latest(1))[0]
    assert (stored.status, stored.notes) == (SetStatus.FAILED, ("no_profile",))
    assert model.calls == 0


async def test_crash_is_recorded_by_type_only(
    sessions: async_sessionmaker[AsyncSession],
    business: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sets = SqlCreativeSets(sessions, business)
    await request_set(sets, RULES, NOW)

    await _run(sessions, FakeModel(RuntimeError("sk-secret ответ модели")))

    stored = (await sets.latest(1))[0]
    assert (stored.status, stored.notes) == (SetStatus.FAILED, ("RuntimeError",))
    assert "sk-secret" not in caplog.text


async def test_stale_running_set_is_retried_until_attempts_run_out(
    sessions: async_sessionmaker[AsyncSession], business: uuid.UUID
) -> None:
    sets = SqlCreativeSets(sessions, business)
    await request_set(sets, RULES, NOW)
    queue = SqlCreativeQueue(sessions)
    # Воркер взял набор и упал: строка осталась «в работе».
    for attempt in range(MAX_ATTEMPTS):
        moment = NOW + attempt * (STALE_AFTER + timedelta(minutes=1))
        assert await queue.claim(moment, moment - STALE_AFTER, MAX_ATTEMPTS) is not None
        fresh = await queue.claim(moment, moment - STALE_AFTER, MAX_ATTEMPTS)
        assert fresh is None, "свежий набор в работе не берётся второй раз"

    later = NOW + MAX_ATTEMPTS * (STALE_AFTER + timedelta(minutes=1))
    assert await _run(sessions, FakeModel(ads(GOOD)), later) == 0
    stored = (await sets.latest(1))[0]
    assert (stored.status, stored.notes) == (SetStatus.FAILED, ("stale",))


async def test_finish_does_not_overwrite_ready_set(
    sessions: async_sessionmaker[AsyncSession], business: uuid.UUID
) -> None:
    sets = SqlCreativeSets(sessions, business)
    set_id = await request_set(sets, RULES, NOW)
    await _run(sessions, FakeModel(ads(GOOD)))

    await SqlCreativeQueue(sessions).fail(set_id, "late_duplicate", NOW)

    assert (await sets.latest(1))[0].status is SetStatus.READY


async def test_concurrent_workers_take_different_sets(
    sessions: async_sessionmaker[AsyncSession], business: uuid.UUID
) -> None:
    sets = SqlCreativeSets(sessions, business)
    await request_set(sets, RULES, NOW)
    await request_set(sets, RULES, NOW + timedelta(seconds=1))
    queue = SqlCreativeQueue(sessions)
    first = await queue.claim(NOW, NOW - STALE_AFTER, MAX_ATTEMPTS)
    second = await queue.claim(NOW, NOW - STALE_AFTER, MAX_ATTEMPTS)
    assert first is not None and second is not None
    assert first.id != second.id
    async with sessions() as session:
        attempts = (await session.execute(text("SELECT attempts FROM creative_sets"))).scalars()
        assert set(attempts) == {1}
