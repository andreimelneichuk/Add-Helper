"""Исход действия автопилота: стало ли после него хуже (docs/04-autopilot.md §6).

Считается кодом, целыми числами: деньги в микрорублях, отношения в базисных пунктах.
Сравнивается цена обращения после действия с ценой в окне до него. Случайный разброс
обращений (шум Пуассона) отделяется порогом: «хуже» только когда сдвиг больше и порога
правил, и шума. Мало данных — «не хватает данных», откат не делаем.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isqrt
from typing import Any

BASIS_POINTS = 10_000
# Метка исхода, у которого откат уже начат: после падения воркера или недоведённого отката
# его не оценивают заново, а доводят (docs/04-autopilot.md §6).
ROLLBACK_STARTED = "rollback_started"


class OutcomeVerdict(StrEnum):
    BETTER = "better"
    SAME = "same"
    WORSE = "worse"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Period:
    clicks: int
    spend: int  # микрорубли
    leads: int


@dataclass(frozen=True)
class OutcomeRules:
    version: str
    # Окно сравнения в днях; «до» берётся тем же числом дней, сдвинутым на неделю назад,
    # чтобы дни недели совпадали.
    window_days: int
    # Меньше кликов после действия или обращений до него — оценку не даём.
    min_clicks: int
    min_leads: int
    worse_threshold_bp: int
    better_threshold_bp: int
    # Ширина шума в десятых долях сигмы: 20 — две сигмы.
    noise_sigmas_x10: int
    # Ноль обращений после действия — «хуже», когда потрачено столько цен обращения «до».
    zero_leads_spend_multiple: int
    # Дней дозревания данных площадки после окна: оцениваем через window_days + maturity_days.
    maturity_days: int = 2
    # Предохранители (domain/safeguards.py).
    # Столько автоотказов за rollback_window_days — и автопилот возвращается в «Предлагает».
    max_rollbacks: int = 2
    rollback_window_days: int = 14
    anomaly_window: int = 5
    anomaly_worse_count: int = 3
    # Сколько дней после срока ждём недостающую статистику; потом «не хватает данных».
    max_wait_days: int = 7
    # Остановки старше этого срока на учёт не берём: после ввода этапа или простоя
    # автопилот не откатывает пачкой то, что владелец давно видел.
    register_lookback_days: int = 14

    def __post_init__(self) -> None:
        """Бессмысленные пороги отвергаем при загрузке правил, а не делением на ноль в бою."""
        positive = {
            "window_days": self.window_days,
            "min_leads": self.min_leads,
            "worse_threshold_bp": self.worse_threshold_bp,
            "better_threshold_bp": self.better_threshold_bp,
            "zero_leads_spend_multiple": self.zero_leads_spend_multiple,
            "max_rollbacks": self.max_rollbacks,
            "rollback_window_days": self.rollback_window_days,
            "anomaly_window": self.anomaly_window,
            "anomaly_worse_count": self.anomaly_worse_count,
        }
        non_negative = {
            "min_clicks": self.min_clicks,
            "noise_sigmas_x10": self.noise_sigmas_x10,
            "maturity_days": self.maturity_days,
            "max_wait_days": self.max_wait_days,
        }
        bad = [name for name, value in positive.items() if value < 1]
        bad += [name for name, value in non_negative.items() if value < 0]
        if bad:
            raise ValueError(f"правила {self.version}: недопустимые значения: {', '.join(bad)}")
        if self.anomaly_worse_count > self.anomaly_window:
            raise ValueError(f"правила {self.version}: anomaly_worse_count больше anomaly_window")
        if self.register_lookback_days < self.window_days + self.maturity_days:
            raise ValueError(f"правила {self.version}: register_lookback_days меньше окна оценки")


@dataclass(frozen=True)
class OutcomeResult:
    verdict: OutcomeVerdict
    # Изменение цены обращения в bp: +10000 — выросла вдвое. None: не посчитать.
    cpl_change_bp: int | None
    noise_bp: int
    reason: str


@dataclass(frozen=True)
class PendingOutcome:
    id: int
    change_id: int
    object_ref: str
    kind: str
    applied_at: datetime
    decision_id: str | None
    evaluate_at: datetime
    # Метка прерванного отката: исход захвачен снова после падения или недоведённого отката.
    note: str | None = None


@dataclass(frozen=True)
class OutcomeRow:
    id: int
    change_id: int
    object_ref: str
    kind: str
    verdict: str | None
    cpl_change_bp: int | None
    noise_bp: int | None
    rollback_change_id: int | None
    note: str | None
    evaluated_at: datetime | None
    measured: Mapping[str, Any] | None


@dataclass(frozen=True)
class Measured:
    before: Period
    after: Period
    # Другие изменения той же кампании в окне: вывод о причине был бы нечестным.
    confounded: bool
    reverted: bool
    # Окно «после» полное: все дни дозрели и в статистике есть за каждый из них.
    complete: bool = True


def _noise_bp(leads_before: int, leads_after: int, sigmas_x10: int) -> int:
    """Относительный разброс отношения двух пуассоновских счётчиков: sqrt(1/a + 1/b)."""
    if leads_before <= 0 or leads_after <= 0:
        return 0
    spread_bp = isqrt(
        BASIS_POINTS * BASIS_POINTS * (leads_before + leads_after) // (leads_before * leads_after)
    )
    return spread_bp * sigmas_x10 // 10


def evaluate_outcome(before: Period, after: Period, rules: OutcomeRules) -> OutcomeResult:
    if before.leads < rules.min_leads or after.clicks < rules.min_clicks or before.spend <= 0:
        return OutcomeResult(OutcomeVerdict.INSUFFICIENT, None, 0, "мало данных")
    if after.leads == 0:
        # Цена обращения «до», с округлением вверх: порог не занижаем.
        price_before = -(-before.spend // before.leads)
        if after.spend >= rules.zero_leads_spend_multiple * price_before:
            return OutcomeResult(
                OutcomeVerdict.WORSE, None, 0, "обращений нет при заметном расходе"
            )
        return OutcomeResult(OutcomeVerdict.INSUFFICIENT, None, 0, "обращений нет, расход мал")
    # Отношение цен: (after.spend / after.leads) / (before.spend / before.leads).
    ratio_bp = after.spend * before.leads * BASIS_POINTS // (before.spend * after.leads)
    change_bp = ratio_bp - BASIS_POINTS
    noise = _noise_bp(before.leads, after.leads, rules.noise_sigmas_x10)
    if change_bp > max(rules.worse_threshold_bp, noise):
        return OutcomeResult(OutcomeVerdict.WORSE, change_bp, noise, "цена обращения выросла")
    if -change_bp > max(rules.better_threshold_bp, noise):
        return OutcomeResult(OutcomeVerdict.BETTER, change_bp, noise, "цена обращения снизилась")
    return OutcomeResult(OutcomeVerdict.SAME, change_bp, noise, "в пределах шума")
