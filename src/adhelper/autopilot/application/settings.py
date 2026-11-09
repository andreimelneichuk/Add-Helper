"""Смена настроек автопилота: проверка, запрет «Сам», строка истории. Площадку не меняет."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from adhelper.autopilot.domain.decide import ActionKind
from adhelper.autopilot.domain.settings import (
    DEFAULT_SETTINGS,
    MSK,
    AutopilotMode,
    AutopilotSettings,
    Cadence,
    SettingsError,
    cycle_mode,
    is_due,
    next_run_at,
    validated,
)

# HTTP-слой и воркер берут эти имена здесь, а не из домена.
__all__ = [
    "DEFAULT_SETTINGS",
    "ActionKind",
    "AutoNotAllowed",
    "AutopilotMode",
    "AutopilotSettings",
    "Cadence",
    "Patch",
    "SettingsError",
    "cycle_mode",
    "demote",
    "is_due",
    "next_run_at",
    "report_text",
    "stop",
    "update_settings",
]

MODE_LINES = {
    AutopilotMode.WATCH: "Перевёл автопилот в режим «Только следит»",
    AutopilotMode.CONFIRM: "Перевёл автопилот в режим «Предлагает»",
    AutopilotMode.AUTO: "Включил самостоятельный режим",
}
STOPPED_LINE = "Остановил автопилот"
RESUMED_LINE = "Снова включил автопилот"
CADENCE_LINES = {
    Cadence.DAILY: "Автопилот разбирает рекламу каждый день",
    Cadence.WEEKLY: "Автопилот разбирает рекламу раз в неделю",
}


class AutoNotAllowed(Exception):
    """Режим «Сам» ещё не разрешён владельцем проекта (AUTOPILOT_AUTO_ALLOWED)."""


class Store(Protocol):
    async def get(self) -> AutopilotSettings | None: ...
    async def save(self, settings: AutopilotSettings, lines: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class Patch:
    mode: AutopilotMode | None = None
    allowed_actions: frozenset[ActionKind] | None = None
    cadence: Cadence | None = None
    run_hour: int | None = None
    envelope: dict[str, int] | None = None
    resume: bool = False


def _lines(before: AutopilotSettings, after: AutopilotSettings) -> list[str]:
    lines: list[str] = []
    if before.stopped_at is not None and after.stopped_at is None:
        lines.append(RESUMED_LINE)
    if after.mode is not before.mode:
        lines.append(MODE_LINES[after.mode])
    if after.cadence is not before.cadence:
        lines.append(CADENCE_LINES[after.cadence])
    if after.run_hour != before.run_hour:
        lines.append(f"Автопилот разбирает рекламу в {after.run_hour}:20 по Москве")
    if after.allowed_actions != before.allowed_actions:
        lines.append("Изменил, что автопилот вправе делать сам")
    if after.envelope != before.envelope:
        lines.append("Изменил рамки автопилота")
    return lines


async def update_settings(store: Store, patch: Patch, *, auto_allowed: bool) -> AutopilotSettings:
    """Применяет изменения. Остановка снимается только явным resume, не сменой расписания."""
    before = await store.get() or DEFAULT_SETTINGS
    if patch.mode is AutopilotMode.AUTO and not auto_allowed:
        raise AutoNotAllowed
    after = replace(
        before,
        mode=patch.mode or before.mode,
        allowed_actions=patch.allowed_actions or before.allowed_actions,
        cadence=patch.cadence or before.cadence,
        run_hour=before.run_hour if patch.run_hour is None else patch.run_hour,
        envelope=before.envelope if patch.envelope is None else patch.envelope,
        stopped_at=None if patch.resume else before.stopped_at,
    )
    validated(after)
    lines = _lines(before, after)
    if after != before:
        await store.save(after, lines)
    return after


async def stop(store: Store, now: datetime) -> AutopilotSettings:
    before = await store.get() or DEFAULT_SETTINGS
    if before.stopped_at is not None:
        return before
    after = replace(before, stopped_at=now)
    await store.save(after, [STOPPED_LINE])
    return after


async def demote(store: Store, reason: str) -> bool:
    """Предохранитель: из «Сам» обратно в «Предлагает». Вернуть может только владелец."""
    before = await store.get() or DEFAULT_SETTINGS
    if before.mode is not AutopilotMode.AUTO:
        return False
    after = replace(before, mode=AutopilotMode.CONFIRM)
    await store.save(after, [f"Выключил самостоятельный режим: {reason}"])
    return True


MODE_WORDS = {
    AutopilotMode.WATCH: "только следит",
    AutopilotMode.CONFIRM: "предлагает, решаете вы",
    AutopilotMode.AUTO: "действует сам",
}


def report_text(settings: AutopilotSettings, recent_lines: Sequence[str]) -> str:
    """Справка «что делает автопилот»: положение, расписание, последние перемены. Пишет код."""
    if settings.stopped_at is not None:
        head = "Автопилот остановлен. Рекламу он не разбирает, сделанное осталось как было."
    else:
        how = "раз в неделю" if settings.cadence is Cadence.WEEKLY else "каждый день"
        head = f"Автопилот включён: {MODE_WORDS[settings.mode]}, разбирает рекламу {how}."
    if settings.last_run_at is None:
        tail = " Первого разбора ещё не было."
    else:
        tail = f" Последний разбор: {settings.last_run_at.astimezone(MSK):%d.%m в %H:%M} (МСК)."
    lines = list(recent_lines[:3])
    recent = (" Последние перемены: " + "; ".join(lines) + ".") if lines else ""
    return head + tail + recent
