import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from adhelper.creatives.application.generate import generate_ads
from adhelper.creatives.domain.model import (
    BusinessProfile,
    CreativeRules,
    DraftDecision,
    Service,
    TextModel,
)
from adhelper.creatives.domain.ports import CreativeQueue, CreativeSets, ProfileSource

logger = logging.getLogger(__name__)

# Правила для новых наборов. creatives-2 на наборе оценки реже снимает чистые объявления,
# чем creatives-1, при той же доле пойманного: docs/05-creatives.md §7.
CURRENT_RULES = "creatives-2"
# Генерация с судьёй укладывается в минуты; дольше — воркер упал посреди набора.
STALE_AFTER = timedelta(minutes=15)
# Каждая попытка тратит вызовы модели, поэтому повторов немного.
MAX_ATTEMPTS = 2
DAY = timedelta(hours=24)


class TooManySets(Exception):
    """Суточный лимит наборов исчерпан."""


async def request_set(sets: CreativeSets, rules: CreativeRules, now: datetime) -> int:
    """Ставит набор в очередь. Строка «в очереди» и есть задача: она в той же транзакции."""
    if await sets.count_since(now - DAY) >= rules.max_sets_per_day:
        raise TooManySets
    return await sets.request(rules.version, now)


def profile_from(
    name: str,
    services: Sequence[tuple[str, int | None]],
    address: str,
    features: Sequence[str],
    niche: str,
) -> BusinessProfile:
    """Профиль из полей формы. Пустые строки отбрасываем: в рекламу они не пойдут."""
    return BusinessProfile(
        name=name,
        services=tuple(Service(n.strip(), price) for n, price in services if n.strip()),
        address=address.strip(),
        features=tuple(f.strip() for f in features if f.strip()),
        niche=niche.strip(),
    )


async def decide_draft(sets: CreativeSets, set_id: int, index: int, approve: bool) -> bool:
    """Решение владельца по черновику. На площадку отсюда ничего не уходит."""
    decision = DraftDecision.APPROVED if approve else DraftDecision.DECLINED
    return await sets.decide(set_id, index, decision)


async def run_queued(
    queue: CreativeQueue,
    profiles: ProfileSource,
    model: TextModel | None,
    judge: TextModel | None,
    load_rules: Callable[[str], CreativeRules],
    clock: Callable[[], datetime],
    limit: int = 5,
) -> int:
    """Разбирает очередь. Повтор безопасен: набор, зависший в работе, берётся заново,
    пока не исчерпаны попытки, а готовый набор второй раз не генерируется.
    """
    done = 0
    for _ in range(limit):
        now = clock()
        claimed = await queue.claim(now, now - STALE_AFTER, MAX_ATTEMPTS)
        if claimed is None:
            break
        profile = await profiles.profile(claimed.business_id)
        if profile is None or not profile.services:
            await queue.fail(claimed.id, "no_profile", clock())
            continue
        try:
            result = await generate_ads(profile, model, load_rules(claimed.rules_version), judge)
        except Exception as error:
            # В логе только тип: текст ошибки может содержать ответ модели или ключ.
            logger.error("Генерация набора %s: %s", claimed.id, type(error).__name__)
            await queue.fail(claimed.id, type(error).__name__, clock())
            continue
        await queue.finish(claimed.id, result, clock())
        done += 1
    return done
