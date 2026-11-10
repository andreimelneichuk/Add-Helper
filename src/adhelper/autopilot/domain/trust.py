"""Лестница доверия: что кабинет вправе делать сам и когда открывается следующая ступень.

docs/17-autopilot-plan.md, этап 9. Ступени открывает только владелец проекта, по одной и не
раньше чем через неделю без срывов. Владелец кабинета сузить доверие может, расширить нет.
Неизвестность никогда не открывает ступень.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from adhelper.autopilot.domain.decide import ActionKind

CLEAN_DAYS = 7
REPORT_DAYS = 14
MSK = timezone(timedelta(hours=3))


class TrustClass(StrEnum):
    PAUSE_AD = "pause_ad"
    START_TEST = "start_test"
    SHIFT_LIMIT = "shift_limit"
    SET_SCHEDULE = "set_schedule"


# Ступени по порядку. Первая даёт только самое обратимое: пауза и тест.
LADDER: tuple[frozenset[TrustClass], ...] = (
    frozenset({TrustClass.PAUSE_AD, TrustClass.START_TEST}),
    frozenset({TrustClass.SHIFT_LIMIT}),
    frozenset({TrustClass.SET_SCHEDULE}),
)
START_CLASSES = LADDER[0]
# Открыть можно, исполнять цикл ещё не умеет: рост лимита идёт карточкой владельцу
# (worker/runtime.py, `_increase_needs_owner`), а планировщик не выдаёт смену расписания.
NOT_EXECUTED = frozenset({TrustClass.SHIFT_LIMIT, TrustClass.SET_SCHEDULE})

# Какому классу доверия соответствует действие цикла. Перенос лимита идёт как рост лимита.
ACTION_CLASS = {
    ActionKind.PAUSE_AD: TrustClass.PAUSE_AD,
    ActionKind.START_TEST: TrustClass.START_TEST,
    ActionKind.INCREASE_LIMIT: TrustClass.SHIFT_LIMIT,
}

# Рамки по умолчанию для первого самостоятельного кабинета: одно изменение в сутки и не больше
# 5% рабочего бюджета в неделю. Рамка только сужает правила policy-N.
FIRST_CABINET_ENVELOPE: Mapping[str, int] = {"max_changes_per_day": 1, "max_weekly_move_bp": 500}


def confined_envelope(envelope: Mapping[str, int]) -> dict[str, int]:
    """Рамки «Сам»: свои значения только уже, чем у первого кабинета; пропущенные берутся оттуда."""
    result = dict(envelope)
    for name, limit in FIRST_CABINET_ENVELOPE.items():
        result[name] = min(result.get(name, limit), limit)
    return result


class GrantRefusal(StrEnum):
    ALREADY = "already"
    NOT_NEXT_STEP = "not_next_step"
    TOO_EARLY = "too_early"
    HAD_FAILURES = "had_failures"
    # Неделя работы без срывов считается только в «Сам» без остановки: вне его работы не было.
    NOT_AUTO = "not_auto"


def effective_allowed(
    allowed: frozenset[ActionKind], granted: frozenset[TrustClass]
) -> frozenset[ActionKind]:
    """Действия, которые цикл вправе сделать сам: отмечено владельцем кабинета и открыто."""
    return frozenset(kind for kind in allowed if ACTION_CLASS[kind] in granted)


def next_step(granted: frozenset[TrustClass]) -> TrustClass | None:
    """Следующая закрытая ступень; None, когда открыто всё."""
    for step in LADDER:
        missing = sorted(step - granted)
        if missing:
            return missing[0]
    return None


def can_grant(
    wanted: TrustClass,
    granted: frozenset[TrustClass],
    since: datetime | None,
    failures: int,
    now: datetime,
) -> GrantRefusal | None:
    """Можно ли открыть класс. `since`: когда открыта последняя ступень; None: не знаем."""
    if wanted in granted:
        return GrantRefusal.ALREADY
    if wanted is not next_step(granted):
        return GrantRefusal.NOT_NEXT_STEP
    if since is None or now - since < timedelta(days=CLEAN_DAYS):
        return GrantRefusal.TOO_EARLY
    if failures > 0:
        return GrantRefusal.HAD_FAILURES
    return None


def narrowed_value(rule_value: int, envelope: Mapping[str, int], name: str) -> int:
    """Рамка кабинета только сужает значение из файла правил."""
    return min(rule_value, envelope[name]) if name in envelope else rule_value


def daily_report_due(auto_since: datetime | None, now: datetime) -> bool:
    """Ежедневное письмо в первые 14 календарных суток по Москве, считая день включения."""
    if auto_since is None:
        return False
    days = (now.astimezone(MSK).date() - auto_since.astimezone(MSK).date()).days
    return 0 <= days < REPORT_DAYS
