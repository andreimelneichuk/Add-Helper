"""Объявления в мастере до входа: docs/11-user-way.md, шаг 4.

Пишет тот же писатель с той же проверкой, но по фактам черновика: бизнеса ещё нет.
Направлений до запуска нет, поэтому объявления остаются в наборе, а владелец выбирает,
какие оставить. Запуск (T6) начнёт с выбранных.
"""

from dataclasses import dataclass

from adhelper.creatives.domain.lifecycle import FactSource, fact_sources
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, CreativeSource, SetStatus

__all__ = [
    "FactSource",
    "WizardDraft",
    "WizardSet",
    "with_sources",
]


@dataclass(frozen=True)
class WizardDraft:
    headline: str
    text: str
    sources: tuple[FactSource, ...]


@dataclass(frozen=True)
class WizardSet:
    id: int
    status: SetStatus
    source: CreativeSource | None
    drafts: tuple[WizardDraft, ...]
    # Чувствительная ниша: каждое объявление перед запуском покажем владельцу.
    needs_review: bool
    notes: tuple[str, ...]


def with_sources(drafts: tuple[AdDraft, ...], profile: BusinessProfile) -> tuple[WizardDraft, ...]:
    return tuple(WizardDraft(d.headline, d.text, fact_sources(d, profile)) for d in drafts)


def kept_is_valid(kept: tuple[int, ...], count: int) -> bool:
    """Оставить нужно хотя бы одно объявление, и только из тех, что написаны."""
    return bool(kept) and len(set(kept)) == len(kept) and all(0 <= i < count for i in kept)
