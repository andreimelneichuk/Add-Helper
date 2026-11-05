from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.rules import BASIS_POINTS, BudgetRules, require_int


@dataclass(frozen=True)
class SpendSnapshot:
    reported: int
    data_as_of: datetime

    def __post_init__(self) -> None:
        require_int(reported=self.reported)
        if self.data_as_of.tzinfo is None:
            raise ValueError("Момент данных должен быть с часовым поясом")


@dataclass(frozen=True)
class BurnRate:
    """Скорость расхода как точная дробь: amount за seconds."""

    amount: int
    seconds: int

    @staticmethod
    def zero() -> BurnRate:
        return BurnRate(amount=0, seconds=1)

    @staticmethod
    def between(older: SpendSnapshot, newer: SpendSnapshot, previous: BurnRate) -> BurnRate:
        """Без новых данных сохраняем прежнюю скорость: расход мог продолжаться."""
        seconds = int((newer.data_as_of - older.data_as_of).total_seconds())
        if seconds <= 0:
            return previous
        return BurnRate(amount=max(newer.reported - older.reported, 0), seconds=seconds)

    def over(self, seconds: int) -> int:
        """Расход за указанное время с округлением вверх."""
        return -(-self.amount * seconds // self.seconds)


def _data_age_seconds(snapshot: SpendSnapshot, now: datetime) -> int:
    return max(int((now - snapshot.data_as_of).total_seconds()), 0)


def corrected_spend(snapshot: SpendSnapshot, burn: BurnRate, now: datetime) -> int:
    return snapshot.reported + burn.over(_data_age_seconds(snapshot, now))


class WatchAction(StrEnum):
    OK = "ok"
    ALERT = "alert"
    STOP = "stop"


@dataclass(frozen=True)
class WatchVerdict:
    action: WatchAction
    projected: int
    next_check_seconds: int
    rules_version: str


def evaluate_watch(
    period: BudgetPeriod,
    snapshot: SpendSnapshot,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
) -> WatchVerdict:
    horizon = (
        _data_age_seconds(snapshot, now) + rules.check_interval_seconds + rules.stop_latency_seconds
    )
    projected = snapshot.reported + burn.over(horizon)

    def reached(threshold_bp: int) -> bool:
        return projected * BASIS_POINTS >= period.hard_limit * threshold_bp

    if reached(rules.stop_threshold_bp):
        action = WatchAction.STOP
    elif reached(rules.alert_threshold_bp):
        action = WatchAction.ALERT
    else:
        action = WatchAction.OK
    return WatchVerdict(
        action=action,
        projected=projected,
        next_check_seconds=(
            rules.check_interval_seconds
            if action is WatchAction.OK
            else rules.alert_check_interval_seconds
        ),
        rules_version=rules.version,
    )


def freshest(snapshot: SpendSnapshot, history: Sequence[SpendSnapshot]) -> SpendSnapshot:
    """Площадка могла вернуть данные старше уже виденных: тогда верим прежним."""
    if history and history[-1].data_as_of > snapshot.data_as_of:
        return history[-1]
    return snapshot


def burn_from_history(snapshots: Sequence[SpendSnapshot]) -> BurnRate:
    """Скорость расхода по двум последним снимкам, от старого к новому."""
    if len(snapshots) < 2:
        return BurnRate.zero()
    return BurnRate.between(snapshots[-2], snapshots[-1], BurnRate.zero())
