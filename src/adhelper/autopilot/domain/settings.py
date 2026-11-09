"""Настройки автопилота бизнеса: положение, расписание, разрешённые действия.

Расписание считается по московскому времени: Директ живёт по нему (docs/04-autopilot.md).
Разбор идёт раз в сутки или раз в неделю в `run_hour`:20 МСК; воркер просыпается каждый час.
docs/17-autopilot-plan.md, этап 1.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum

from adhelper.autopilot.domain.decide import ActionKind, Mode

MSK = timezone(timedelta(hours=3))
SLOT_MINUTE = 20
WEEK_DAYS = 7


class AutopilotMode(StrEnum):
    """Что владелец разрешил: только следить, предлагать или действовать самому."""

    WATCH = "watch"
    CONFIRM = "confirm"
    AUTO = "auto"


class Cadence(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class SettingsError(ValueError):
    """Значение настройки не подходит."""


@dataclass(frozen=True)
class AutopilotSettings:
    mode: AutopilotMode = AutopilotMode.CONFIRM
    # Что автопилот вправе сделать сам, когда включён режим «Сам». В режиме «Предлагает»
    # владелец видит все предложения, как и раньше.
    allowed_actions: frozenset[ActionKind] = frozenset({ActionKind.PAUSE_AD, ActionKind.START_TEST})
    cadence: Cadence = Cadence.DAILY
    run_hour: int = 6
    # Рамки словами: целые числа (доли в bp, дни, копейки). Этап 3 наполнит их смыслом.
    envelope: Mapping[str, int] = field(default_factory=dict)
    stopped_at: datetime | None = None
    last_run_at: datetime | None = None


DEFAULT_SETTINGS = AutopilotSettings()


def validated(settings: AutopilotSettings) -> AutopilotSettings:
    if not 0 <= settings.run_hour <= 23:
        raise SettingsError("Час разбора должен быть от 0 до 23")
    if not settings.allowed_actions:
        raise SettingsError("Разрешите хотя бы одно действие")
    for name, value in settings.envelope.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SettingsError(f"Рамка «{name}» должна быть целым числом не меньше нуля")
    return settings


def cycle_mode(mode: AutopilotMode, *, auto_allowed: bool) -> Mode:
    """Режим цикла. Пока владелец проекта не разрешил «Сам», он работает как «Предлагает»."""
    if mode is AutopilotMode.WATCH:
        return Mode.PAUSE
    if mode is AutopilotMode.AUTO and auto_allowed:
        return Mode.AUTO
    return Mode.CONFIRM


def _slot(day: datetime, run_hour: int) -> datetime:
    return day.astimezone(MSK).replace(hour=run_hour, minute=SLOT_MINUTE, second=0, microsecond=0)


def _latest_slot(settings: AutopilotSettings, now: datetime) -> datetime:
    slot = _slot(now, settings.run_hour)
    return slot if slot <= now else slot - timedelta(days=1)


def is_due(settings: AutopilotSettings, now: datetime) -> bool:
    """Пора ли разбирать. Повтор в тот же слот ничего не запускает: опирается на last_run_at."""
    if settings.stopped_at is not None:
        return False
    last = settings.last_run_at
    slot = _latest_slot(settings, now)
    if last is None:
        # Ни разу не разбирали: ждём слот сегодняшнего дня, как раньше ждал суточный цикл.
        return slot.astimezone(MSK).date() == now.astimezone(MSK).date()
    if last >= slot:
        return False
    if settings.cadence is Cadence.WEEKLY:
        return (slot.date() - last.astimezone(MSK).date()).days >= WEEK_DAYS
    return True


def next_run_at(settings: AutopilotSettings, now: datetime) -> datetime | None:
    """Ближайший слот разбора в UTC; None, если автопилот остановлен."""
    if settings.stopped_at is not None:
        return None
    last = settings.last_run_at
    if last is None:
        today = _slot(now, settings.run_hour)
        return today.astimezone(UTC)
    if settings.cadence is Cadence.WEEKLY:
        day = last.astimezone(MSK) + timedelta(days=WEEK_DAYS)
        slot = _slot(day, settings.run_hour)
        return slot.astimezone(UTC) if slot > last else (slot + timedelta(days=1)).astimezone(UTC)
    slot = _slot(last, settings.run_hour)
    while slot <= last:
        slot += timedelta(days=1)
    return slot.astimezone(UTC)
