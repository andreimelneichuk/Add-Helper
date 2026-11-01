"""Жизненный цикл объявлений без участия владельца: docs/05-creatives.md §8.

Код решает, когда писать и что ставить на место; модель только пишет текст.
Владелец нужен в редких случаях, они возвращаются как `attention`.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from adhelper.creatives.domain.checks import WORD, _numbers, profile_text, similarity_bp
from adhelper.creatives.domain.model import AdDraft, BusinessProfile

# Доля значимых слов особенности или адреса, при которой считаем, что объявление на них опирается.
CITED_BP = 6000
BASIS_POINTS = 10000


class CreativeState(StrEnum):
    READY = "ready"
    RESERVE = "reserve"
    # Заняло место и ждёт отправки в Директ: отправку сделает адаптер площадки (T6).
    LAUNCH_PENDING = "launch_pending"
    NEEDS_OWNER = "needs_owner"
    STOPPED = "stopped"
    WITHDRAWN = "withdrawn"


# Эти объявления ещё не на площадке: их можно двигать без журнала.
WAITING = (
    CreativeState.READY,
    CreativeState.RESERVE,
    CreativeState.LAUNCH_PENDING,
    CreativeState.NEEDS_OWNER,
)
# Занимают место в направлении.
PLACED = (CreativeState.LAUNCH_PENDING, CreativeState.NEEDS_OWNER)


class WriteReason(StrEnum):
    NEW_CAMPAIGN = "new_campaign"
    FACT_CHANGED = "fact_changed"
    REFILL = "refill"


@dataclass(frozen=True)
class SlotRules:
    version: str
    slots_per_campaign: int
    reserve_max: int
    similarity_max_bp: int


@dataclass(frozen=True)
class FactSource:
    """Откуда в объявлении факт: фрагмент текста и поле профиля."""

    fragment: str
    label: str
    key: str


@dataclass(frozen=True)
class CampaignSlots:
    account: str
    campaign_id: str
    running: bool
    # Тексты объявлений, которые сейчас идут на площадке.
    live: tuple[AdDraft, ...]
    ever_had_ads: bool


@dataclass(frozen=True)
class Candidate:
    id: int
    account: str
    campaign_id: str
    state: CreativeState
    draft: AdDraft
    # Можно запускать без владельца: шаблон из профиля или текст, прошедший судью.
    auto_ok: bool
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Move:
    creative_id: int
    state: CreativeState
    reason: str


@dataclass(frozen=True)
class Request:
    account: str
    campaign_id: str
    reason: WriteReason
    wanted: int


@dataclass(frozen=True)
class SlotPlan:
    moves: tuple[Move, ...]
    requests: tuple[Request, ...]
    attention: tuple[str, ...]


def _significant(text: str) -> set[str]:
    return {word.lower() for word in WORD.findall(text) if len(word) >= 3 or word.isdigit()}


def _cited(phrase: str, lowered: str, words: set[str]) -> bool:
    if not phrase.strip():
        return False
    if phrase.lower() in lowered:
        return True
    own = _significant(phrase)
    return bool(own) and len(own & words) * BASIS_POINTS >= CITED_BP * len(own)


def _profile_sources(profile: BusinessProfile) -> list[FactSource]:
    sources: list[FactSource] = []
    for service in profile.services:
        if service.price is not None:
            sources.append(
                FactSource(
                    f"{service.price} ₽",
                    f"цена услуги «{service.name}»",
                    f"price:{service.name}:{service.price}",
                )
            )
        sources.append(FactSource(service.name, "услуга из профиля", f"service:{service.name}"))
    for feature in profile.features:
        sources.append(FactSource(feature, "особенность из профиля", f"feature:{feature}"))
    if profile.address:
        sources.append(
            FactSource(profile.address, "адрес из профиля", f"address:{profile.address}")
        )
    sources.append(FactSource(profile.name, "название из профиля", f"name:{profile.name}"))
    return sources


def fact_sources(draft: AdDraft, profile: BusinessProfile) -> tuple[FactSource, ...]:
    """Каждая цена и утверждение объявления со ссылкой на поле профиля."""
    full = f"{draft.headline} {draft.text}"
    lowered = full.lower()
    words = _significant(full)
    numbers = _numbers(full)
    found: list[FactSource] = []
    for source in _profile_sources(profile):
        if source.key.startswith("price:"):
            if re.sub(r"\D", "", source.fragment) in numbers:
                found.append(source)
        elif _cited(source.fragment, lowered, words):
            found.append(source)
    return tuple(found)


def unconfirmed_numbers(draft: AdDraft, profile: BusinessProfile) -> tuple[str, ...]:
    """Числа объявления, которых нет в профиле: цена изменилась или её не было."""
    full = f"{draft.headline} {draft.text}"
    return tuple(sorted(_numbers(full) - _numbers(profile_text(profile))))


def is_stale(candidate: Candidate, profile: BusinessProfile) -> bool:
    """Факт, на который опиралось объявление, из профиля пропал или изменился."""
    if unconfirmed_numbers(candidate.draft, profile):
        return True
    current = {source.key for source in _profile_sources(profile)}
    return any(key not in current for key in candidate.facts)


def _similar(draft: AdDraft, others: Iterable[AdDraft], rules: SlotRules) -> bool:
    return any(similarity_bp(draft, other) > rules.similarity_max_bp for other in others)


def plan_slots(
    profile: BusinessProfile,
    campaigns: Sequence[CampaignSlots],
    candidates: Sequence[Candidate],
    rules: SlotRules,
    open_sets: frozenset[tuple[str, str]],
    sets_left: int,
    sensitive: bool,
) -> SlotPlan:
    """Что сделать за цикл: снять устаревшее, занять свободные места, заказать новые тексты.

    Повтор безопасен: план строится из текущего состояния и сам ничего не меняет.
    """
    moves: list[Move] = []
    requests: list[Request] = []
    stale_in: set[tuple[str, str]] = set()
    fresh: list[Candidate] = []
    for item in candidates:
        if item.state not in WAITING:
            continue
        if is_stale(item, profile):
            moves.append(Move(item.id, CreativeState.STOPPED, "fact_changed"))
            stale_in.add((item.account, item.campaign_id))
        else:
            fresh.append(item)

    attention: list[str] = []
    if not profile.services:
        attention.append("ads_no_profile")

    for campaign in campaigns:
        key = (campaign.account, campaign.campaign_id)
        own = sorted((c for c in fresh if (c.account, c.campaign_id) == key), key=lambda c: c.id)
        placed = [c for c in own if c.state in PLACED]
        if not campaign.running:
            continue
        taken = [*campaign.live, *(c.draft for c in placed)]
        free = rules.slots_per_campaign - len(taken)
        reserve = 0
        for item in (c for c in own if c.state not in PLACED):
            if _similar(item.draft, taken, rules):
                moves.append(Move(item.id, CreativeState.STOPPED, "too_similar"))
                continue
            if free > 0:
                target = (
                    CreativeState.LAUNCH_PENDING
                    if item.auto_ok and not sensitive
                    else CreativeState.NEEDS_OWNER
                )
                moves.append(Move(item.id, target, "place_free"))
                taken.append(item.draft)
                free -= 1
            elif reserve < rules.reserve_max:
                reserve += 1
                taken.append(item.draft)
                if item.state is not CreativeState.RESERVE:
                    moves.append(Move(item.id, CreativeState.RESERVE, "no_place"))
            else:
                moves.append(Move(item.id, CreativeState.STOPPED, "reserve_full"))
        if free > 0 and profile.services and key not in open_sets and sets_left > 0:
            if key in stale_in:
                reason = WriteReason.FACT_CHANGED
            elif not campaign.ever_had_ads and not own:
                reason = WriteReason.NEW_CAMPAIGN
            else:
                reason = WriteReason.REFILL
            requests.append(Request(campaign.account, campaign.campaign_id, reason, free))
            sets_left -= 1

    needs_owner = any(m.state is CreativeState.NEEDS_OWNER for m in moves) or any(
        c.state is CreativeState.NEEDS_OWNER for c in fresh
    )
    if needs_owner:
        attention.append("ads_need_confirmation")
    return SlotPlan(tuple(moves), tuple(requests), tuple(attention))


@dataclass(frozen=True)
class NewCreative:
    """Объявление из готового набора: ещё без места, с источниками фактов."""

    draft: AdDraft
    auto_ok: bool
    sources: tuple[FactSource, ...]


def new_creatives(
    drafts: Sequence[AdDraft], profile: BusinessProfile, auto_ok: bool
) -> tuple[NewCreative, ...]:
    return tuple(NewCreative(d, auto_ok, fact_sources(d, profile)) for d in drafts)


@dataclass(frozen=True)
class HistoryEntry:
    at: datetime
    state: str
    reason: str


@dataclass(frozen=True)
class StoredCreative:
    id: int
    account: str
    campaign_id: str
    headline: str
    text: str
    source: str
    state: CreativeState
    reason: str
    state_reason: str
    sources: tuple[FactSource, ...]
    history: tuple[HistoryEntry, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WritingSet:
    """Набор, который сейчас пишется для места в направлении."""

    id: int
    account: str
    campaign_id: str
    reason: str
    wanted: int
    status: str
    created_at: datetime


@dataclass(frozen=True)
class Audience:
    """Кому показываем: настройки направления на площадке. Пустое поле — площадка не задала."""

    placement: str = ""
    regions: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    schedule: str = ""


@dataclass(frozen=True)
class CampaignInfo:
    name: str
    running: bool
    audience: Audience


@dataclass(frozen=True)
class AdBasis:
    """На чём держится текст: поля профиля и числа, которых в профиле нет."""

    sources: tuple[FactSource, ...]
    unconfirmed: tuple[str, ...]


def ad_basis(draft: AdDraft, profile: BusinessProfile | None) -> AdBasis:
    if profile is None:
        return AdBasis((), tuple(sorted(_numbers(f"{draft.headline} {draft.text}"))))
    return AdBasis(fact_sources(draft, profile), unconfirmed_numbers(draft, profile))
