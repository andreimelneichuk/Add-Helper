"""Экономика бизнеса для экрана: числа, цель по услугам и «как посчитано». Текст пишет код."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from adhelper.business.domain.economics import (
    BP,
    Economics,
    EconomicsError,
    EconomicsRules,
    goal_micros,
    reference,
    validated,
)

__all__ = [
    "BP",
    "Economics",
    "EconomicsError",
    "EconomicsRules",
    "Patch",
    "ServiceGoal",
    "View",
    "build_view",
    "confirmed_goal_micros",
    "reference",
    "save_patch",
]

MICROS = 1_000_000


@dataclass(frozen=True)
class ServiceGoal:
    service: str
    price_rubles: int
    goal_rubles: int


@dataclass(frozen=True)
class View:
    economics: Economics
    check_rubles: int | None
    check_from_profile: bool
    goal_rubles: int | None
    services: tuple[ServiceGoal, ...]
    how: str


@dataclass(frozen=True)
class Patch:
    avg_check_rubles: int | None = None
    lead_to_client_bp: int | None = None
    margin_bp: int | None = None
    repeat_bp: int | None = None
    ad_share_bp: int | None = None
    capacity_per_week: int | None = None
    confirm: bool = False


class Stored(Protocol):
    @property
    def economics(self) -> Economics: ...
    @property
    def avg_check_micros(self) -> int | None: ...
    @property
    def confirmed_at(self) -> datetime | None: ...


class Store(Protocol):
    async def get(self) -> Stored | None: ...
    async def save(
        self, economics: Economics, avg_check_micros: int | None, confirmed_at: datetime | None
    ) -> None: ...


def _rubles(micros: int) -> int:
    """Округление только при показе: к ближайшему рублю, половина вверх."""
    return (micros + MICROS // 2) // MICROS


def _pct(bp: int) -> str:
    text = f"{bp / 100:g}".replace(".", ",")
    return f"{text}%"


def _money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def build_view(
    economics: Economics,
    avg_check_micros: int | None,
    prices: Sequence[tuple[str, int]],
) -> View:
    """`prices` — услуги профиля: название и цена в рублях. Чек: названный или средняя цена."""
    priced = [(name, price) for name, price in prices if price > 0]
    from_profile = avg_check_micros is None
    if avg_check_micros is None and priced:
        avg_check_micros = sum(price for _, price in priced) * MICROS // len(priced)
    goal = goal_micros(avg_check_micros, economics)
    services = tuple(
        ServiceGoal(name, price, _rubles(goal_micros(price * MICROS, economics) or 0))
        for name, price in priced
    )
    how = _how(economics, avg_check_micros, goal) if goal is not None else ""
    return View(
        economics=economics,
        check_rubles=_rubles(avg_check_micros) if avg_check_micros else None,
        check_from_profile=from_profile and avg_check_micros is not None,
        goal_rubles=_rubles(goal) if goal is not None else None,
        services=services,
        how=how,
    )


def _how(economics: Economics, check_micros: int | None, goal: int | None) -> str:
    assert check_micros is not None and goal is not None
    return (
        f"Средний чек {_money(_rubles(check_micros))} × прибыль с чека {_pct(economics.margin_bp)} "
        f"× клиентами становятся {_pct(economics.lead_to_client_bp)} обращений "
        f"× повторные визиты +{_pct(economics.repeat_bp)} "
        f"× на рекламу идёт {_pct(economics.ad_share_bp)} прибыли "
        f"= {_money(_rubles(goal))} за обращение."
    )


def _pick[T](new: T | None, old: T) -> T:
    return old if new is None else new


async def save_patch(
    store: Store, patch: Patch, rules: EconomicsRules, now: datetime
) -> tuple[Economics, int | None, datetime | None]:
    """Меняет названные поля. Смена чисел снимает подтверждение, если его не повторили."""
    stored = await store.get()
    before = stored.economics if stored else reference(rules)
    check = stored.avg_check_micros if stored else None
    after = replace(
        before,
        lead_to_client_bp=_pick(patch.lead_to_client_bp, before.lead_to_client_bp),
        margin_bp=_pick(patch.margin_bp, before.margin_bp),
        repeat_bp=_pick(patch.repeat_bp, before.repeat_bp),
        ad_share_bp=_pick(patch.ad_share_bp, before.ad_share_bp),
        capacity_per_week=_pick(patch.capacity_per_week, before.capacity_per_week),
    )
    if patch.avg_check_rubles is not None:
        if patch.avg_check_rubles <= 0:
            raise EconomicsError("avg_check")
        check = patch.avg_check_rubles * MICROS
    after = validated(replace(after, confirmed=False), rules)
    changed = after != replace(before, confirmed=False) or check != (
        stored.avg_check_micros if stored else None
    )
    was = stored.confirmed_at if stored else None
    confirmed_at = now if patch.confirm else (None if changed else was)
    await store.save(replace(after, confirmed=confirmed_at is not None), check, confirmed_at)
    return replace(after, confirmed=confirmed_at is not None), check, confirmed_at


async def confirmed_goal_micros(store: Store, legacy: int) -> int:
    """Цель цикла автопилота: из подтверждённой экономики, иначе прежняя цель.

    Без подтверждения цифры остаются «ориентиром» и цикл по ним не работает.
    Средний чек берётся только названный владельцем: цена из профиля цель не меняет.
    """
    stored = await store.get()
    if stored is None or stored.confirmed_at is None or stored.avg_check_micros is None:
        return legacy
    return goal_micros(stored.avg_check_micros, stored.economics) or legacy
