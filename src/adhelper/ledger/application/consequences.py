"""Что случится после отмены: слова для владельца до нажатия, docs/04-autopilot.md §6.

Текст собирает код из снимка «до» в журнале, числа не пишет модель.
"""

from collections.abc import Mapping
from typing import Any

from adhelper.campaigns.domain.targeting import Schedule, describe_schedule
from adhelper.ledger.application.rollback import RollbackResult, RollbackStatus
from adhelper.ledger.domain.model import Change, ChangeKind

MICROS_PER_RUB = 1_000_000
MONEY_TAIL = "Потраченные деньги не вернутся, общий бюджет не вырастет."
REFUSALS = {
    RollbackStatus.NOT_FOUND: "Изменение не найдено",
    RollbackStatus.NOT_APPLIED: "Это изменение не было применено, отменять нечего",
    RollbackStatus.NOT_REVERSIBLE: "Это действие нельзя отменить",
    RollbackStatus.ALREADY_REVERTED: "Это уже отменено",
    RollbackStatus.HAS_LATER_CHANGES: "После него были другие изменения. Сначала отмените их",
    RollbackStatus.CHANGED_OUTSIDE: "Это уже изменили в кабинете Яндекса, отмена его перезапишет",
    RollbackStatus.PENDING: "Яндекс сейчас не отвечает. Проверю и отменю позже, если попросите",
    RollbackStatus.POLICY_REJECTED: "Отмена вышла бы за рамки бюджета, поэтому я её не выполню",
    RollbackStatus.FAILED: "Яндекс не принял отмену. Прежнее значение осталось",
}


def _rubles(micros: int) -> str:
    whole = -(-micros // MICROS_PER_RUB)
    return f"{whole:,}".replace(",", " ") + " ₽"


def _directions(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} направление"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} направления"
    return f"{count} направлений"


def _restored(change: Change, regions: Mapping[int, str]) -> str:
    before: Mapping[str, Any] = change.before
    if change.kind is ChangeKind.SET_LIMIT and isinstance(before.get("limit"), int):
        return f"Лимит направления вернётся к {_rubles(before['limit'])}."
    if change.kind is ChangeKind.PAUSE_AD:
        return "Объявление снова включится и разделит бюджет с другими."
    if change.kind is ChangeKind.RESUME_AD:
        return "Объявление снова выключится, его деньги получат другие."
    if change.kind is ChangeKind.RESUME:
        return "Показы снова остановятся, заявок с рекламы не будет."
    if change.kind is ChangeKind.SET_SCHEDULE and "schedule" in before:
        text = describe_schedule(Schedule.from_planned(before["schedule"]))
        return f"Вернутся прежние часы показа: {text}."
    if change.kind is ChangeKind.SET_REGIONS and isinstance(before.get("regions"), list):
        names = ", ".join(regions.get(r, str(r)) for r in before["regions"])
        return f"Вернётся прежний регион показа: {names}."
    return "Вернётся прежнее значение."


def consequence(result: RollbackResult, group: int, regions: Mapping[int, str]) -> str:
    """Последствия, если отмена возможна; иначе причина, почему нельзя."""
    if result.status is not RollbackStatus.READY or result.change is None:
        return REFUSALS.get(result.status, "Это действие нельзя отменить")
    where = (
        f" Просьба меняла {_directions(group)}: можно отменить для всех или только для этого."
        if group > 1
        else ""
    )
    return f"{_restored(result.change, regions)}{where} {MONEY_TAIL}"
