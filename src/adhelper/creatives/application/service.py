import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from adhelper.creatives.application.generate import generate_ads
from adhelper.creatives.application.rework import rework_ad
from adhelper.creatives.domain.lifecycle import (
    WAITING,
    AdBasis,
    Audience,
    CreativeState,
    FactPlan,
    FactSource,
    LiveAd,
    SlotRules,
    StoredCreative,
    WritingSet,
    ad_basis,
    fact_sources,
    new_creatives,
    plan_fact_updates,
    plan_slots,
    unconfirmed_numbers,
)
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeRules,
    CreativeSource,
    Service,
    TextModel,
)
from adhelper.creatives.domain.ports import (
    AdSlots,
    Attention,
    CreativeQueue,
    Creatives,
    ProfileSource,
)
from adhelper.creatives.domain.rework import ReworkRules

logger = logging.getLogger(__name__)

# Правила для новых наборов. creatives-3 (рамка писателя) прошла ворота выпуска 2026-11-05:
# верхняя граница ошибки 0,87% при пороге 3%, docs/12-agents.md §4.
CURRENT_RULES = "creatives-3"
CURRENT_SLOT_RULES = "ads-1"
CURRENT_REWORK_RULES = "rework-1"
# Генерация с судьёй укладывается в минуты; дольше — воркер упал посреди набора.
STALE_AFTER = timedelta(minutes=15)
# Каждая попытка тратит вызовы модели, поэтому повторов немного.
MAX_ATTEMPTS = 2
DAY = timedelta(hours=24)
ATTENTION_KINDS = ("ads_no_profile", "ads_need_confirmation")


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


def cited_facts(profile: BusinessProfile, headline: str, text: str) -> tuple[FactSource, ...]:
    return fact_sources(AdDraft(headline, text), profile)


async def run_queued(
    queue: CreativeQueue,
    profiles: ProfileSource,
    model: TextModel | None,
    judge: TextModel | None,
    load_rules: Callable[[str], CreativeRules],
    clock: Callable[[], datetime],
    limit: int = 5,
    load_rework: Callable[[str], ReworkRules] | None = None,
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
        profile = claimed.profile
        if profile is None and claimed.business_id is not None:
            profile = await profiles.profile(claimed.business_id)
        if profile is None or not profile.services:
            await queue.fail(claimed.id, "no_profile", clock())
            continue
        try:
            rules = load_rules(claimed.rules_version)
            if claimed.rework is not None and claimed.rework_rules and load_rework is not None:
                result = await rework_ad(
                    profile, claimed.rework, model, rules, load_rework(claimed.rework_rules), judge
                )
            else:
                result = await generate_ads(profile, model, rules, judge, claimed.campaign_name)
        except Exception as error:
            # В логе только тип: текст ошибки может содержать ответ модели или ключ.
            logger.error("Генерация набора %s: %s", claimed.id, type(error).__name__)
            await queue.fail(claimed.id, type(error).__name__, clock())
            continue
        # Без владельца запускаем шаблоны из профиля и тексты, прошедшие судью.
        auto_ok = result.source is CreativeSource.TEMPLATE or result.judged
        await queue.finish(
            claimed.id, result, clock(), new_creatives(result.drafts, profile, auto_ok)
        )
        done += 1
    return done


async def keep_ads(
    business_id: UUID,
    slots: AdSlots,
    profiles: ProfileSource,
    attention: Attention,
    slot_rules: SlotRules,
    creative_rules: CreativeRules,
    now: datetime,
    rework_rules: str = CURRENT_REWORK_RULES,
) -> FactPlan:
    """Один цикл хранителя: снять устаревшее, занять места, заказать тексты, заменить
    работающие объявления, где факт в профиле изменился.

    Повтор безопасен: план строится из текущих строк, переходы проверяют исходное состояние.
    """
    profile = await profiles.profile(business_id) or BusinessProfile("", ())
    sets_left = creative_rules.max_sets_per_day - await slots.sets_since(now - DAY)
    plan = plan_slots(
        profile,
        await slots.campaigns(),
        await slots.candidates(),
        slot_rules,
        await slots.open_sets(),
        sets_left,
        profile.niche.strip().lower() in creative_rules.sensitive_niches,
    )
    await slots.apply(plan, creative_rules.version, now)
    facts = FactPlan((), (), ())
    if profile.services:
        facts = plan_fact_updates(
            profile,
            await slots.live_ads(),
            await slots.replacements(),
            await slots.rewriting(),
            sets_left - len(plan.requests),
        )
        await slots.apply_facts(facts, creative_rules.version, rework_rules, now)
    for kind in ATTENTION_KINDS:
        if kind in plan.attention:
            await attention.raise_alert(kind, {})
        else:
            await attention.resolve(kind)
    return facts


@dataclass(frozen=True)
class OutdatedAd:
    """Работающее объявление с числом, которого в профиле больше нет."""

    ad: LiveAd
    numbers: tuple[str, ...]
    # Замена пишется или уже ждёт отправки; иначе лимит наборов на сутки исчерпан.
    replacing: bool


async def refresh_after_profile(
    business_id: UUID,
    slots: AdSlots,
    profiles: ProfileSource,
    attention: Attention,
    slot_rules: SlotRules,
    creative_rules: CreativeRules,
    now: datetime,
) -> list[OutdatedAd]:
    """Владелец сохранил профиль: цикл хранителя сразу, а не через 5 минут.

    Возвращает объявления, которые показывают старый факт, чтобы владелец знал о них.
    """
    facts = await keep_ads(business_id, slots, profiles, attention, slot_rules, creative_rules, now)
    profile = await profiles.profile(business_id)
    if profile is None:
        return []
    later = {ad.ad_id for ad in facts.later}
    outdated = []
    for ad in await slots.live_ads():
        numbers = unconfirmed_numbers(ad.draft, profile)
        if numbers:
            replacing = bool(profile.services) and ad.ad_id not in later
            outdated.append(OutdatedAd(ad, numbers, replacing))
    return outdated


class WrongState(Exception):
    """Объявление уже не в том состоянии, в котором его видел владелец."""


async def withdraw(creatives: Creatives, creative_id: int, now: datetime) -> None:
    """Владелец снимает объявление до запуска. На площадке ещё ничего нет, журнал не нужен."""
    if not await creatives.move(creative_id, WAITING, CreativeState.WITHDRAWN, "owner", now):
        raise WrongState


async def confirm(creatives: Creatives, creative_id: int, now: datetime) -> None:
    """Владелец подтверждает объявление, которое без него запускать нельзя."""
    allowed = (CreativeState.NEEDS_OWNER,)
    if not await creatives.move(
        creative_id, allowed, CreativeState.LAUNCH_PENDING, "owner_confirmed", now
    ):
        raise WrongState


@dataclass(frozen=True)
class Board:
    """Экран «Объявления»: что готовится, ждёт владельца или снято, и что пишется сейчас."""

    creatives: list[tuple[StoredCreative, str | None]]
    writing: list[tuple[WritingSet, str | None]]


@dataclass(frozen=True)
class CreativeReport:
    creative: StoredCreative
    campaign_name: str | None
    audience: Audience
    basis: AdBasis


async def board(creatives: Creatives) -> Board:
    names = {key: info.name for key, info in (await creatives.campaigns()).items()}
    return Board(
        [(c, names.get((c.account, c.campaign_id))) for c in await creatives.all()],
        [(w, names.get((w.account, w.campaign_id))) for w in await creatives.writing()],
    )


async def creative_report(
    creatives: Creatives, profile: BusinessProfile | None, creative_id: int
) -> CreativeReport | None:
    found = await creatives.get(creative_id)
    if found is None:
        return None
    info = (await creatives.campaigns()).get((found.account, found.campaign_id))
    return CreativeReport(
        found,
        info.name if info else None,
        info.audience if info else Audience(),
        # Сверяем с профилем сейчас: так видно, что факт с тех пор изменился.
        ad_basis(AdDraft(found.headline, found.text), profile),
    )


async def live_ad_context(
    creatives: Creatives,
    profile: BusinessProfile | None,
    account: str,
    campaign_id: str,
    headline: str,
    text: str,
) -> tuple[Audience, AdBasis]:
    """Кому показываем объявление с площадки и на какие поля профиля оно опирается."""
    info = (await creatives.campaigns()).get((account, campaign_id))
    return (info.audience if info else Audience()), ad_basis(AdDraft(headline, text), profile)
