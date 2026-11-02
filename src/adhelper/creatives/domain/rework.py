"""Переделка одного объявления по просьбе владельца: docs/05-creatives.md, шаг 19б пути.

Просьба — данные. Код до модели проверяет, что в ней нет фактов вне профиля, а после модели
проверяет каждый вариант теми же правилами, что и новые объявления.
"""

from dataclasses import dataclass
from enum import StrEnum

from adhelper.creatives.domain.checks import check_draft
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeRules,
    Rework,
    ViolationKind,
)

__all__ = ["Rework", "ReworkRules", "WishProblem", "WishRefusal", "check_wish"]

# Эти нарушения в просьбе значат «факт, которого нет в профиле».
FACT_KINDS = (
    ViolationKind.UNCONFIRMED_NUMBER,
    ViolationKind.UNCONFIRMED_CLAIM,
    ViolationKind.FORBIDDEN_WORD,
)


@dataclass(frozen=True)
class ReworkRules:
    version: str
    # Сколько вариантов показываем рядом со старым.
    variants: int
    wish_max_chars: int


class WishProblem(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class WishRefusal:
    kind: WishProblem
    # Числа и слова-обещания из просьбы, которых нет в профиле.
    items: tuple[str, ...] = ()


def check_wish(
    wish: str, profile: BusinessProfile, rules: CreativeRules, rework: ReworkRules
) -> WishRefusal | None:
    text = wish.strip()
    if not text:
        return WishRefusal(WishProblem.EMPTY)
    if len(text) > rework.wish_max_chars:
        return WishRefusal(WishProblem.TOO_LONG)
    # Заголовок-заглушка не пустой, чтобы проверка смотрела только факты просьбы.
    found = check_draft(AdDraft("-", text), profile, rules)
    items = tuple(dict.fromkeys(v.detail for v in found if v.kind in FACT_KINDS))
    return WishRefusal(WishProblem.UNCONFIRMED, items) if items else None
