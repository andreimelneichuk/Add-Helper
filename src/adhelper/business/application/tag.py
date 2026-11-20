"""Проверка кода Метрики: воркер скачивает главную страницу, код ищет номер счётчика."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol

from adhelper.business.domain.ports import SiteFetcher, SiteUnavailable
from adhelper.business.domain.tag import (
    StoredTagCheck,
    TagResult,
    TagRules,
    TagStatus,
    find_city_counters,
    find_counters,
    find_default_city_id,
)

__all__ = [
    "StoredTagCheck",
    "TagResult",
    "TagRules",
    "TagStatus",
    "check_tag",
    "find_city_counters",
    "find_counters",
    "find_default_city_id",
    "run_tag_checks",
]

STALE_AFTER = timedelta(minutes=10)
logger = logging.getLogger(__name__)


class TagCheckQueue(Protocol):
    async def claim(
        self, now: datetime, stale_before: datetime, max_attempts: int
    ) -> tuple[int, str] | None: ...
    async def finish(self, check_id: int, result: TagResult, now: datetime) -> None: ...


async def check_tag(url: str, fetcher: SiteFetcher) -> TagResult:
    try:
        page = await fetcher.fetch(url)
    except SiteUnavailable as error:
        return TagResult(TagStatus.FAILED, problem=error.problem.value)
    if page.counters:
        return TagResult(
            TagStatus.FOUND,
            tuple(page.counters),
            city_counters=page.city_counters,
            default_city_id=page.default_city_id,
        )
    return TagResult(
        TagStatus.MISSING,
        city_counters=page.city_counters,
        default_city_id=page.default_city_id,
    )


async def run_tag_checks(
    queue: TagCheckQueue,
    fetcher: SiteFetcher,
    rules: TagRules,
    clock: Callable[[], datetime],
    limit: int = 10,
    on_finished: Callable[[int], Awaitable[None]] | None = None,
) -> int:
    done = 0
    for _ in range(limit):
        now = clock()
        claimed = await queue.claim(now, now - STALE_AFTER, rules.max_attempts)
        if claimed is None:
            break
        check_id, url = claimed
        try:
            result = await check_tag(url, fetcher)
        except Exception as error:
            logger.error("Проверка кода %s: %s", check_id, type(error).__name__)
            continue
        await queue.finish(check_id, result, clock())
        if on_finished is not None:
            try:
                await on_finished(check_id)
            except Exception as error:
                logger.warning("Синхронизация счётчика %s: %s", check_id, type(error).__name__)
        done += 1
    return done
