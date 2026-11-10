"""Открытие и закрытие ступеней доверия. Делает только владелец проекта, из команды.

Здесь меняются настройки в нашей базе, площадка не затрагивается. Ступень открывается по
одной, не раньше чем через неделю после предыдущей и только без срывов (domain/trust.py).
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from adhelper.autopilot.domain.settings import (
    DEFAULT_SETTINGS,
    AutopilotMode,
    AutopilotSettings,
)
from adhelper.autopilot.domain.trust import (
    NOT_EXECUTED,
    START_CLASSES,
    GrantRefusal,
    TrustClass,
    can_grant,
    daily_report_due,
    effective_allowed,
    next_step,
)

CLASS_WORDS = {
    TrustClass.PAUSE_AD: "останавливать объявления",
    TrustClass.START_TEST: "запускать проверку двух вариантов",
    TrustClass.SHIFT_LIMIT: "переносить и поднимать лимиты",
    TrustClass.SET_SCHEDULE: "менять расписание показов",
}
REFUSAL_WORDS = {
    GrantRefusal.ALREADY: "Этот класс уже открыт",
    GrantRefusal.NOT_NEXT_STEP: "Ступени открываются по очереди: сначала предыдущая",
    GrantRefusal.TOO_EARLY: (
        "Ещё не прошла неделя работы без срывов или неизвестно, когда она началась"
    ),
    GrantRefusal.HAD_FAILURES: (
        "С открытия прошлой ступени были срывы: откат, сбой или срыв бюджета"
    ),
    GrantRefusal.NOT_AUTO: (
        "Кабинет сейчас не в режиме «Сам» или остановлен: неделя работы без срывов "
        "считается только в нём"
    ),
}


class GrantDenied(Exception):
    def __init__(self, refusal: GrantRefusal | None, message: str) -> None:
        super().__init__(message)
        self.refusal = refusal


class Store(Protocol):
    async def get(self) -> AutopilotSettings | None: ...
    async def save(
        self,
        settings: AutopilotSettings,
        lines: Sequence[str],
        before: AutopilotSettings | None = None,
    ) -> None: ...


class Facts(Protocol):
    async def failures_since(self, since: datetime) -> int: ...


async def grant(store: Store, facts: Facts, wanted: TrustClass, now: datetime) -> AutopilotSettings:
    before = await store.get() or DEFAULT_SETTINGS
    if before.mode is not AutopilotMode.AUTO or before.stopped_at is not None:
        raise GrantDenied(GrantRefusal.NOT_AUTO, REFUSAL_WORDS[GrantRefusal.NOT_AUTO])
    # Неделя идёт от последнего из двух: открытия прошлой ступени и входа кабинета в «Сам».
    starts = [moment for moment in (before.granted_at, before.auto_since) if moment is not None]
    since = max(starts) if starts else None
    failures = await facts.failures_since(since) if since is not None else 0
    refusal = can_grant(wanted, before.granted, since, failures, now)
    if refusal is not None:
        raise GrantDenied(refusal, REFUSAL_WORDS[refusal])
    after = replace(before, granted=before.granted | {wanted}, granted_at=now)
    await store.save(
        after, [f"Открыл автопилоту новое: {CLASS_WORDS[wanted]} самому"], before=before
    )
    return after


async def revoke(store: Store, wanted: TrustClass, now: datetime) -> AutopilotSettings:
    """Закрывает класс и всё выше. Ожидание неделя заново: ступень открывается снова по правилам."""
    if wanted in START_CLASSES:
        raise GrantDenied(None, "Первую ступень не закрыть: остановите автопилот или режим «Сам»")
    before = await store.get() or DEFAULT_SETTINGS
    if wanted not in before.granted:
        # Класс и так закрыт: ожидание заново не начинаем и в историю ничего не пишем.
        raise GrantDenied(None, "Этот класс и так закрыт")
    keep = frozenset(item for item in before.granted if item in START_CLASSES)
    step = next_step(keep)
    while step is not None and step is not wanted and step in before.granted:
        keep |= {step}
        step = next_step(keep)
    after = replace(before, granted=keep, granted_at=now)
    await store.save(after, [f"Закрыл автопилоту: {CLASS_WORDS[wanted]} самому"], before=before)
    return after


# HTTP-слой берёт имя здесь, а не из домена.
__all__ = [
    "CLASS_WORDS",
    "NOT_EXECUTED",
    "GrantDenied",
    "TrustClass",
    "daily_report_due",
    "effective_allowed",
    "grant",
    "revoke",
]
