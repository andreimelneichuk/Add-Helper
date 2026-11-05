"""Очередь разборов сайта на настоящем PostgreSQL: лимит, изоляция, повтор после сбоя."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.adapters.site_reads import SqlSiteReadQueue, SqlSiteReads, TooManyReads
from adhelper.business.application.site import ReadStatus, run_site_reads
from tests.business.test_read_site import GOOD, HOME, PRICE, Model, Site
from tests.seed import seed_account

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
RULES = load_site_rules()
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def anna(sessions: Sessions) -> uuid.UUID:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id)
    return business_id


@pytest.fixture
async def boris(sessions: Sessions) -> uuid.UUID:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id)
    return business_id


async def _run(sessions: Sessions, model: Model | None, now: datetime = NOW) -> int:
    return await run_site_reads(
        SqlSiteReadQueue(sessions),
        Site(HOME, PRICE),
        model,
        "qwen-test",
        RULES,
        lambda version: RULES,
        lambda: now,
    )


async def test_request_is_queued_once_and_read_by_worker(
    sessions: Sessions, anna: uuid.UUID
) -> None:
    reads = SqlSiteReads(sessions, anna)
    first = await reads.request("https://nogti.ru/", RULES, NOW)
    # Пока разбор ждёт, повторная просьба не ставит второй и не тратит лимит.
    assert await reads.request("https://nogti.ru/", RULES, NOW) == first
    waiting = await reads.latest()
    assert waiting is not None and (waiting.id, waiting.status) == (first, ReadStatus.QUEUED)

    assert await _run(sessions, Model(GOOD)) == 1
    done = await reads.latest()
    assert done is not None and done.status is ReadStatus.READY
    assert [(f.field.value, f.value, f.price) for f in done.facts] == [
        ("niche", "маникюр", None),
        ("service", "Маникюр с покрытием", 1500),
    ]
    assert done.pages == ("https://nogti.ru/", "https://nogti.ru/price")
    async with sessions() as session:
        row = (
            await session.execute(
                text("SELECT model, rules_version, tokens_used, dropped FROM site_reads")
            )
        ).one()
    assert tuple(row) == ("qwen-test", "site-3", 321, {"quote_not_on_page": 1})
    # Готовый разбор второй раз не берётся.
    assert await _run(sessions, Model(GOOD)) == 0


async def test_failure_is_stored_with_reason(sessions: Sessions, anna: uuid.UUID) -> None:
    reads = SqlSiteReads(sessions, anna)
    await reads.request("https://nogti.ru/", RULES, NOW)
    await _run(sessions, None)
    failed = await reads.latest()
    assert failed is not None
    assert (failed.status, failed.problem) == (ReadStatus.FAILED, "no_model")


async def test_daily_limit(sessions: Sessions, anna: uuid.UUID) -> None:
    reads = SqlSiteReads(sessions, anna)
    for hour in range(RULES.max_reads_per_day):
        await reads.request(f"https://nogti.ru/{hour}", RULES, NOW + timedelta(hours=hour))
        await _run(sessions, None, NOW + timedelta(hours=hour))
    with pytest.raises(TooManyReads):
        await reads.request("https://nogti.ru/x", RULES, NOW + timedelta(hours=5))
    # Окно скользящее: через сутки после первого можно снова.
    await reads.request("https://nogti.ru/x", RULES, NOW + timedelta(hours=24, minutes=1))


async def test_reads_are_isolated_by_business(
    sessions: Sessions, anna: uuid.UUID, boris: uuid.UUID
) -> None:
    await SqlSiteReads(sessions, anna).request("https://nogti.ru/", RULES, NOW)
    assert await SqlSiteReads(sessions, boris).latest() is None


async def test_stale_read_is_retried_then_failed(sessions: Sessions, anna: uuid.UUID) -> None:
    reads = SqlSiteReads(sessions, anna)
    await reads.request("https://nogti.ru/", RULES, NOW)
    queue = SqlSiteReadQueue(sessions)
    # Воркер взял разбор и упал, не дописав.
    assert await queue.claim(NOW, NOW - timedelta(minutes=15), 2) is not None
    later = NOW + timedelta(minutes=16)
    assert await queue.claim(later, later - timedelta(minutes=15), 2) is not None
    last = NOW + timedelta(minutes=40)
    assert await queue.claim(last, last - timedelta(minutes=15), 2) is None
    stored = await reads.latest()
    assert stored is not None and (stored.status, stored.problem) == (ReadStatus.FAILED, "stale")
