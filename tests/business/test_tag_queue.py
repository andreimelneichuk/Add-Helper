"""Очередь проверок кода: смена версии правил перезапускает старые проверки."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.business.adapters.tag_checks import SqlTagCheckQueue, SqlTagChecks
from adhelper.business.domain.tag import TagResult, TagRules, TagStatus
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.domain.model import BusinessProfile
from tests.seed import seed_account

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
OLD = TagRules("tag-1", 20, 2)
NEW = TagRules("tag-2", 20, 2)


async def test_stale_site_is_requeued_after_rules_version_change(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    business = uuid.uuid4()
    await seed_account(sessions, business, "acc")
    await SqlProfiles(sessions, business).save(
        BusinessProfile(name="Тест", services=()), "https://example.ru/"
    )
    await SqlTagChecks(sessions, business).request("https://example.ru/", OLD, NOW)
    queue = SqlTagCheckQueue(sessions)
    claimed = await queue.claim(NOW, NOW - timedelta(minutes=10), 2)
    assert claimed is not None
    await queue.finish(claimed[0], TagResult(TagStatus.MISSING), NOW)

    # Проверка старой версии не считается актуальной.
    assert await queue.stale_sites(NEW.version) == [(business, "https://example.ru/")]

    await SqlTagChecks(sessions, business).request("https://example.ru/", NEW, NOW)
    # Новая проверка уже в очереди: второй раз не ставим.
    assert await queue.stale_sites(NEW.version) == []
