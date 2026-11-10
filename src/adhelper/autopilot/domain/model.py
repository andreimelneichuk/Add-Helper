import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from adhelper.autopilot.domain.health import Window
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.budget.domain.calendar import platform_date

BASIS_POINTS = 10000


class Verdict(StrEnum):
    EFFICIENT = "efficient"
    INEFFICIENT = "inefficient"
    BORDERLINE = "borderline"
    LOW_DATA = "low_data"


@dataclass(frozen=True)
class DailyStat:
    day: date
    spend: int
    leads: int
    clicks: int
    # None: визиты неизвестны (Метрика не подключена), а не ноль.
    visits: int | None
    trusted: bool = True


@dataclass(frozen=True)
class Totals:
    spend: int
    leads: int
    clicks: int = 0


@dataclass(frozen=True)
class AdStats:
    ref: str
    campaign_id: str
    group_id: str
    spend: int
    leads: int
    age_days: int
    active: bool
    rollback_hold: bool = False
    # Владелец недавно отказался от теста этого объявления.
    test_hold: bool = False
    # Клики дозревших дней: нужны оценке по ожидаемой потере (autopilot-3).
    clicks: int = 0


@dataclass(frozen=True)
class CampaignStats:
    campaign_id: str
    limit: int
    budget_limited: bool
    last_change_at: datetime | None
    test_running: bool = False
    # None: площадка не сказала. Политика трактует неизвестное строго.
    auto_strategy: bool | None = None
    in_learning: bool | None = None


@dataclass(frozen=True)
class AccountData:
    """Всё, что цикл читает о кабинете за один проход."""

    ads: Sequence[AdStats]
    campaigns: Sequence[CampaignStats]
    recent: Window
    baseline: Window
    platform_incident: bool
    manual_hold: bool


@dataclass(frozen=True)
class Posterior:
    p_good_bp: int
    p_bad_bp: int


@dataclass(frozen=True)
class Evaluation:
    verdict: Verdict
    posterior: Posterior


def mature_totals(rows: Iterable[DailyStat], now: datetime, rules: AutopilotRules) -> Totals:
    """Только дозревшие дни, которым можно верить. Граница суток — московская, как у Директа."""
    until = platform_date(now) - timedelta(days=rules.maturity_days)
    used = [row for row in rows if row.day <= until and row.trusted]
    return Totals(
        sum(row.spend for row in used),
        sum(row.leads for row in used),
        sum(row.clicks for row in used),
    )


def _poisson_cdf(count: int, mean: float) -> float:
    """P(X ≤ count) в логарифмах: при большом числе обращений прямой расчёт переполняется."""
    if mean <= 0:
        return 1.0
    log_mean = math.log(mean)
    terms = [math.exp(k * log_mean - mean - math.lgamma(k + 1)) for k in range(count + 1)]
    return min(1.0, math.fsum(terms))


def _to_bp(probability: float) -> int:
    """Вниз: порог действия должен быть достигнут честно. Полной уверенности не бывает,
    а у самого края сумма плавает из-за округления, поэтому верхняя граница 9999."""
    return max(0, min(BASIS_POINTS - 1, math.floor(probability * BASIS_POINTS)))


def posterior(spend: int, leads: int, target: int, rules: AutopilotRules) -> Posterior:
    """Гамма-Пуассон ; для целого a это точная сумма Пуассона.

    Вероятности вещественные, но деньги на входе целые и результат детерминирован.
    """
    shape = rules.prior_weight + leads
    rate = rules.prior_weight * target + spend
    good = _poisson_cdf(shape - 1, rate / target)
    bad_cpl = target * rules.bad_cpl_multiple_bp / BASIS_POINTS
    bad = 1.0 - _poisson_cdf(shape - 1, rate / bad_cpl)
    return Posterior(_to_bp(good), _to_bp(bad))


def classify(stats: AdStats, target: int, rules: AutopilotRules) -> Evaluation:
    result = posterior(stats.spend, stats.leads, target, rules)
    learning_spend = rules.learning_target_multiple * target
    if stats.age_days < rules.learning_days and stats.spend < learning_spend:
        return Evaluation(Verdict.LOW_DATA, result)
    if stats.spend < target:
        return Evaluation(Verdict.LOW_DATA, result)
    if (
        result.p_bad_bp >= rules.bad_probability_bp
        and stats.spend >= rules.bad_min_spend_multiple * target
    ):
        return Evaluation(Verdict.INEFFICIENT, result)
    if result.p_good_bp >= rules.good_probability_bp:
        return Evaluation(Verdict.EFFICIENT, result)
    return Evaluation(Verdict.BORDERLINE, result)


@dataclass(frozen=True)
class AdDay:
    """Один день объявления для симулятора."""

    ref: str
    campaign_id: str
    group_id: str
    spend: int
    leads: int
    clicks: int
    visits: int | None


@dataclass(frozen=True)
class Day:
    day: date
    ads: Sequence[AdDay]
    platform_incident: bool = False


@dataclass(frozen=True)
class History:
    """Записанные дни кабинета и текущие лимиты кампаний."""

    days: list[Day]
    limits: dict[str, int]
