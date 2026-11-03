import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

BASIS_POINTS = 10000


class Status(StrEnum):
    REVIEW = "review"
    RUNNING = "running"
    PAUSED = "paused"
    APPLIED = "applied"
    CANCELLED = "cancelled"


class Hypothesis(StrEnum):
    """Закрытый список: один параметр за тест."""

    OFFER = "offer"
    PRICE = "price"
    BENEFIT = "benefit"
    SOCIAL_PROOF = "social_proof"
    IMAGE = "image"
    CALL_TO_ACTION = "call_to_action"


# Какие факты профиля нужны гипотезе. Скидок, отзывов и изображений профиль не хранит,
# поэтому такие гипотезы не предлагаются: факты в рекламе только из профиля.
NEEDS_PRICES = frozenset({Hypothesis.PRICE})
NEEDS_FEATURES = frozenset({Hypothesis.BENEFIT})
NEEDS_NOTHING = frozenset({Hypothesis.CALL_TO_ACTION})


def supported_hypotheses(has_prices: bool, has_features: bool) -> set[Hypothesis]:
    supported = set(NEEDS_NOTHING)
    if has_prices:
        supported |= NEEDS_PRICES
    if has_features:
        supported |= NEEDS_FEATURES
    return supported


def pick_hypothesis(supported: set[Hypothesis], tried: set[str]) -> Hypothesis | None:
    """Первая по закрытому списку, которую ещё не проверяли на этом объявлении."""
    for hypothesis in Hypothesis:
        if hypothesis in supported and hypothesis.value not in tried:
            return hypothesis
    return None


class ModerationState(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class Outcome(StrEnum):
    CONTINUE = "continue"
    WINNER = "winner"
    NO_DIFFERENCE = "no_difference"
    LOW_DATA = "low_data"


class IllegalTransition(Exception):
    pass


class TestAlreadyRunning(Exception):
    __test__ = False


ALLOWED = {
    Status.REVIEW: {Status.RUNNING, Status.CANCELLED},
    Status.RUNNING: {Status.PAUSED, Status.APPLIED, Status.CANCELLED},
    Status.PAUSED: {Status.RUNNING, Status.CANCELLED},
    Status.APPLIED: set[Status](),
    Status.CANCELLED: set[Status](),
}


def transition(current: Status, target: Status) -> Status:
    if target not in ALLOWED[current]:
        raise IllegalTransition(f"{current.value} -> {target.value}")
    return target


@dataclass(frozen=True)
class ExperimentRules:
    version: str
    win_probability_bp: int
    lose_probability_bp: int
    min_days: int
    max_days: int
    min_clicks_per_variant: int
    max_test_spend: int
    exclusion_hours: int


@dataclass(frozen=True)
class VariantStats:
    clicks: int
    successes: int
    spend: int


@dataclass(frozen=True)
class Evaluation:
    outcome: Outcome
    winner: str | None
    probability_b_bp: int
    explanation: str


@dataclass(frozen=True)
class ExperimentRecord:
    id: int
    account: str
    campaign_id: str
    control_ref: str
    variant_ref: str | None
    hypothesis: str
    status: Status
    reason: str | None
    expected: str
    switch_to: str | None
    switch_seq: int
    winner: str | None
    explanation: str | None
    rules_version: str
    started_at: datetime | None


class ExperimentStore(Protocol):
    async def add(
        self,
        account: str,
        campaign_id: str,
        control_ref: str,
        variant_ref: str | None,
        hypothesis: str,
        rules_version: str,
    ) -> int: ...
    async def get(self, experiment_id: int) -> ExperimentRecord | None: ...
    async def set_status(
        self,
        experiment_id: int,
        status: Status,
        reason: str | None = None,
        started_at: datetime | None = None,
        winner: str | None = None,
        explanation: str | None = None,
    ) -> None: ...
    async def begin_switch(self, experiment_id: int, target: str) -> None: ...
    async def finish_switch(self, experiment_id: int, target: str) -> None: ...


def balanced_schedule(days: int) -> str:
    return "".join(active_variant(day) for day in range(days))


def active_variant(day: int) -> str:
    """Последовательность Морса: A и B поровну и без длинных серий, дни недели перемешаны."""
    return "B" if day.bit_count() % 2 else "A"


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def probability_b_better(a: VariantStats, b: VariantStats) -> float:
    """Точная формула для бета-распределений: один и тот же вход всегда даёт один результат.

    Выборка из распределения дала бы разные решения при повторном расчёте.
    """
    alpha_a, beta_a = 1 + a.successes, 1 + a.clicks - a.successes
    alpha_b, beta_b = 1 + b.successes, 1 + b.clicks - b.successes
    terms = [
        math.exp(
            _log_beta(alpha_a + i, beta_a + beta_b)
            - math.log(beta_b + i)
            - _log_beta(1 + i, beta_b)
            - _log_beta(alpha_a, beta_a)
        )
        for i in range(alpha_b)
    ]
    return max(0.0, min(1.0, math.fsum(terms)))


def _cheaper(a: VariantStats, b: VariantStats) -> str:
    """Цена результата сравнивается без деления, в целых числах; при равенстве контроль."""
    if b.successes == 0:
        return "A"
    if a.successes == 0:
        return "B"
    return "B" if b.spend * a.successes < a.spend * b.successes else "A"


def evaluate(
    a: VariantStats, b: VariantStats, days: int, spent: int, rules: ExperimentRules
) -> Evaluation:
    probability_bp = math.floor(probability_b_better(a, b) * BASIS_POINTS)
    enough_clicks = min(a.clicks, b.clicks) >= rules.min_clicks_per_variant
    decisive = (
        probability_bp >= rules.win_probability_bp or probability_bp <= rules.lose_probability_bp
    )
    if days >= rules.min_days and enough_clicks and decisive:
        winner = "B" if probability_bp >= rules.win_probability_bp else "A"
        confidence = probability_bp if winner == "B" else BASIS_POINTS - probability_bp
        name = "новый вариант" if winner == "B" else "прежний вариант"
        text = f"С вероятностью {confidence // 100}% {name} лучше, его и оставили."
        return Evaluation(Outcome.WINNER, winner, probability_bp, text)
    if days < rules.max_days and spent < rules.max_test_spend:
        return Evaluation(Outcome.CONTINUE, None, probability_bp, "")
    if not enough_clicks:
        text = "Данных мало, проверка не подтвердила улучшение. Оставили прежний вариант."
        return Evaluation(Outcome.LOW_DATA, "A", probability_bp, text)
    cheaper = _cheaper(a, b)
    text = "Разница мала, поэтому оставили вариант с меньшей ценой обращения."
    if cheaper == "A" and a.spend * b.successes == b.spend * a.successes:
        text = "Разница мала, поэтому оставили прежний вариант."
    return Evaluation(Outcome.NO_DIFFERENCE, cheaper, probability_bp, text)
