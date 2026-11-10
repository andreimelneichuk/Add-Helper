"""Недельный отчёт теневого периода (docs/17-autopilot-plan.md, этап 8).

Чистая арифметика над целыми числами: доли в базисных пунктах, никаких вызовов площадки и
моделей. Пороги ворот этапа 9 лежат в файле `gates-N.toml`; пока владелец их не подтвердил,
ворота никогда не показывают «пройдено».
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum

BASIS_POINTS = 10_000
SAMPLE_SIZE = 10
MSK = timezone(timedelta(hours=3))


class GateStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"
    # Нечего считать: решений или предложений за неделю не было. Это не «пройдено».
    NO_DATA = "no_data"
    # Порог ещё не подтвердил владелец: числа показываем, вердикт не выносим.
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class GateThresholds:
    version: str
    # Автооткатов не больше этой доли самостоятельных действий (bp).
    max_rollback_share_bp: int
    # Принятых владельцем предложений не меньше этой доли (bp).
    min_accepted_share_bp: int
    # Пропущенных часовых тиков воркера за неделю не больше.
    max_missed_ticks: int
    # Значения по умолчанию «задаёт владелец» (этап 8, ворота E); до подтверждения вердикта нет.
    confirmed_by_owner: bool = False

    def __post_init__(self) -> None:
        problems = []
        # Строка "false" из файла правил была бы истиной: подтверждение только настоящим булевым.
        if not isinstance(self.confirmed_by_owner, bool):
            problems.append("confirmed_by_owner должен быть true или false")
        for name in ("max_rollback_share_bp", "min_accepted_share_bp"):
            if not 0 <= getattr(self, name) <= BASIS_POINTS:
                problems.append(f"{name} вне 0..{BASIS_POINTS}")
        if self.max_missed_ticks < 0:
            problems.append("max_missed_ticks не может быть отрицательным")
        if problems:
            raise ValueError(f"Пороги {self.version}: " + "; ".join(problems))


@dataclass(frozen=True)
class WeekMetrics:
    # Предложения владельцу (карточки) и сколько из них он принял.
    proposals: int
    accepted: int
    # Самостоятельные действия автопилота и автооткаты среди них.
    auto_actions: int
    auto_rollbacks: int
    # Оценённые исходы: всего, «хуже контроля» и «данных мало».
    judged: int
    worse: int
    insufficient: int
    # Пары «рабочее решение — теневое» за сутки и сколько из них разошлись.
    shadow_pairs: int
    diverged: int
    # Часовые тики воркера: сколько ждали и сколько пропущено.
    ticks_expected: int
    ticks_missed: int
    # Действия, которые смотрел рецензент: ровно столько платных вызовов моделей сделал автопилот.
    reviewed_actions: int


@dataclass(frozen=True)
class GateLine:
    name: str
    status: GateStatus
    value_bp: int | None
    limit_bp: int | None


def share_bp(part: int, whole: int) -> int | None:
    """Доля в базисных пунктах; None, когда делить не на что."""
    if whole <= 0:
        return None
    return part * BASIS_POINTS // whole


def _line(name: str, value: int | None, limit: int, *, at_most: bool, confirmed: bool) -> GateLine:
    if value is None:
        return GateLine(name, GateStatus.NO_DATA, None, limit)
    if not confirmed:
        return GateLine(name, GateStatus.UNCONFIRMED, value, limit)
    good = value <= limit if at_most else value >= limit
    return GateLine(name, GateStatus.OK if good else GateStatus.FAIL, value, limit)


def evaluate_gates(metrics: WeekMetrics, thresholds: GateThresholds) -> list[GateLine]:
    confirmed = thresholds.confirmed_by_owner
    return [
        _line(
            "rollbacks",
            share_bp(metrics.auto_rollbacks, metrics.auto_actions),
            thresholds.max_rollback_share_bp,
            at_most=True,
            confirmed=confirmed,
        ),
        _line(
            "accepted",
            share_bp(metrics.accepted, metrics.proposals),
            thresholds.min_accepted_share_bp,
            at_most=False,
            confirmed=confirmed,
        ),
        # Нет ни одного ожидаемого тика: журнал пуст или тики старше окна. «Пропущено 0» тут
        # отсутствие данных, а не доказательство работы воркера.
        _line(
            "worker",
            metrics.ticks_missed if metrics.ticks_expected > 0 else None,
            thresholds.max_missed_ticks,
            at_most=True,
            confirmed=confirmed,
        ),
    ]


def count_divergence(
    working: Mapping[tuple[str, str], str], shadow: Mapping[tuple[str, str], str]
) -> tuple[int, int]:
    """(пар сравнили, из них разошлись): ключ — объявление и сутки, значение — действие или wait."""
    both = working.keys() & shadow.keys()
    return len(both), sum(working[key] != shadow[key] for key in both)


def pick_sample(ids: Sequence[int], seed: str, count: int = SAMPLE_SIZE) -> list[int]:
    """Случайные для разбора, но воспроизводимые: порядок задаёт хеш от зерна недели и номера."""
    ranked = sorted(set(ids), key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())
    return sorted(ranked[:count])


def week_seed(now: datetime) -> str:
    """Зерно недели: понедельник текущей недели по Москве, как дата."""
    local = now.astimezone(MSK)
    return (local - timedelta(days=local.weekday())).date().isoformat()


def week_start(now: datetime) -> datetime:
    """Начало текущей недели по Москве (понедельник 00:00), в UTC."""
    local = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    return (local - timedelta(days=local.weekday())).astimezone(UTC)


def sum_metrics(items: Sequence[WeekMetrics]) -> WeekMetrics:
    """Сумма по кабинетам. Тики воркера общие для всех: берём наибольшие, а не складываем."""
    fields = (
        "proposals", "accepted", "auto_actions", "auto_rollbacks", "judged", "worse",
        "insufficient", "shadow_pairs", "diverged", "reviewed_actions",
    )  # fmt: skip
    totals = {name: sum(getattr(item, name) for item in items) for name in fields}
    return WeekMetrics(
        **totals,
        ticks_expected=max((item.ticks_expected for item in items), default=0),
        ticks_missed=max((item.ticks_missed for item in items), default=0),
    )
