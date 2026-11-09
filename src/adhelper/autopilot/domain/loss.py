"""Математика оценки по ожидаемой потере: docs/16-autopilot-autonomy.md §11.

Доля обращений на клик объявления — Beta-Binomial: клики это испытания, обращения успехи,
априор — среднее направления с весом в «воображаемых кликах». Интегралы берутся по сетке
фиксированного размера, без случайности: одинаковый вход всегда даёт одинаковый ответ.
Вещественные числа здесь только внутри оценки; наружу выходят целые базисные пункты и микрорубли.
"""

import math
from dataclasses import dataclass

BASIS_POINTS = 10000
GRID = 2000
# Выше этого счётчики пропорционально уменьшаем: сетка не вырождается.
COUNT_CAP = 10**9
SPREAD = 10.0


@dataclass(frozen=True)
class RateBelief:
    """Beta(alpha, beta) для доли обращений на клик."""

    alpha: float
    beta: float


@dataclass(frozen=True)
class PairResult:
    # Вероятность, что первый вариант хуже второго, в базисных пунктах, округлена вниз.
    p_worse_bp: int
    # Ожидаемый недобор обращений на клик, если оставить первый вместо второго.
    expected_gap: float


def belief(
    clicks: int, leads: int, group_clicks: int, group_leads: int, prior_clicks: int
) -> RateBelief:
    """Апостериор объявления. Группа с поправкой Лапласа: среднее строго внутри (0, 1)."""
    clicks = max(0, clicks)
    leads = max(0, min(leads, clicks))
    prior_rate = (max(0, group_leads) + 1) / (max(0, group_clicks) + 2)
    # Априор с нулевым весом оставил бы нулевые клики без оценки: вес не меньше одного клика.
    prior = max(1, prior_clicks)
    scale = min(1.0, COUNT_CAP / max(1, clicks))
    return RateBelief(
        leads * scale + prior * prior_rate,
        (clicks - leads) * scale + prior * (1.0 - prior_rate),
    )


def rate_mean(value: RateBelief) -> float:
    return value.alpha / (value.alpha + value.beta)


def _sd(value: RateBelief) -> float:
    total = value.alpha + value.beta
    return math.sqrt(value.alpha * value.beta / (total * total * (total + 1.0)))


def _weights(value: RateBelief, low: float, step: float) -> list[float]:
    log_norm = (
        math.lgamma(value.alpha + value.beta) - math.lgamma(value.alpha) - math.lgamma(value.beta)
    )
    raw = []
    for i in range(GRID):
        x = low + (i + 0.5) * step
        log_density = (
            log_norm + (value.alpha - 1.0) * math.log(x) + (value.beta - 1.0) * math.log1p(-x)
        )
        raw.append(math.exp(log_density))
    total = math.fsum(raw)
    if not math.isfinite(total) or total <= 0:
        return [1.0 / GRID] * GRID
    return [item / total for item in raw]


def _range(*values: RateBelief) -> tuple[float, float]:
    low = min(rate_mean(v) - SPREAD * _sd(v) for v in values)
    high = max(rate_mean(v) + SPREAD * _sd(v) for v in values)
    low, high = max(0.0, low), min(1.0, high)
    if high - low < 1e-12:
        low, high = max(0.0, low - 1e-9), min(1.0, high + 1e-9)
    return low, high


def _to_bp(probability: float) -> int:
    """Вниз, с потолком 9999: полной уверенности не бывает."""
    return max(0, min(BASIS_POINTS - 1, math.floor(probability * BASIS_POINTS)))


def compare_rates(worse: RateBelief, better: RateBelief) -> PairResult:
    """Вероятность, что `worse` хуже `better`, и ожидаемый недобор, если оставить `worse`."""
    low, high = _range(worse, better)
    step = (high - low) / GRID
    weak = _weights(worse, low, step)
    strong = _weights(better, low, step)
    below = 0.0
    probability = 0.0
    for i in range(GRID):
        probability += strong[i] * (below + 0.5 * weak[i])
        below += weak[i]
    # Для каждой точки слабого варианта: сколько в среднем лучший выше неё.
    tail_mass = 0.0
    tail_moment = 0.0
    gap_by_cell = [0.0] * GRID
    for i in range(GRID - 1, -1, -1):
        x = low + (i + 0.5) * step
        gap_by_cell[i] = tail_moment - x * tail_mass
        tail_mass += strong[i]
        tail_moment += strong[i] * x
    gap = math.fsum(weak[i] * gap_by_cell[i] for i in range(GRID))
    return PairResult(_to_bp(probability), max(0.0, gap))


def rate_interval(value: RateBelief, tail_bp: int) -> tuple[float, float]:
    """Интервал доли обращений: хвост `tail_bp` отрезан с каждой стороны."""
    low, high = _range(value)
    step = (high - low) / GRID
    weights = _weights(value, low, step)
    wanted = (tail_bp / BASIS_POINTS, 1.0 - tail_bp / BASIS_POINTS)
    found = [low, high]
    cumulative = 0.0
    index = 0
    for i, weight in enumerate(weights):
        before = cumulative
        cumulative += weight
        while index < 2 and cumulative >= wanted[index]:
            share = 0.0 if weight == 0 else (wanted[index] - before) / weight
            found[index] = low + (i + share) * step
            index += 1
        if index == 2:
            break
    return found[0], found[1]


def cost_interval_micros(
    spend: int, clicks: int, low_rate: float, high_rate: float
) -> tuple[int, int]:
    """Интервал цены обращения в микрорублях: цена клика, делённая на долю обращений."""
    if clicks <= 0 or high_rate <= 0:
        return 0, 0
    per_click = spend / clicks
    low = math.floor(per_click / high_rate)
    high = math.ceil(per_click / low_rate) if low_rate > 0 else 10**15
    return low, high


def expected_loss_micros(weekly_clicks: int, gap: float, target: int) -> int:
    """Недополученные за неделю обращения, умноженные на допустимую цену обращения."""
    if weekly_clicks <= 0 or gap <= 0:
        return 0
    return math.floor(weekly_clicks * gap * target)
