"""Экономика бизнеса и допустимая цена обращения. docs/16-autopilot-autonomy.md, «Окупается ли
обращение»; docs/17-autopilot-plan.md, этап 3.

Всё в целых числах: деньги в микрорублях, доли в базисных пунктах (10 000 = 100%). Округляем вниз
и только один раз, в конце: допустимая цена не должна оказаться больше посчитанной.
"""

from dataclasses import dataclass

BP = 10_000


class EconomicsError(ValueError):
    """Значение вне границ из правил `economics-N`."""


@dataclass(frozen=True)
class EconomicsRules:
    version: str
    # Значения-ориентиры, пока владелец ничего не подтвердил.
    lead_to_client_bp: int
    margin_bp: int
    repeat_bp: int
    ad_share_bp: int
    # Верхняя граница повторных визитов: 30 000 bp = ещё три визита на клиента.
    max_repeat_bp: int
    max_capacity_per_week: int


@dataclass(frozen=True)
class Economics:
    lead_to_client_bp: int
    margin_bp: int
    repeat_bp: int
    ad_share_bp: int
    capacity_per_week: int | None
    # Пока владелец не подтвердил числа, это ориентир: автопилот по нему выше «Предлагает» не идёт.
    confirmed: bool


def reference(rules: EconomicsRules) -> Economics:
    return Economics(
        lead_to_client_bp=rules.lead_to_client_bp,
        margin_bp=rules.margin_bp,
        repeat_bp=rules.repeat_bp,
        ad_share_bp=rules.ad_share_bp,
        capacity_per_week=None,
        confirmed=False,
    )


def validated(economics: Economics, rules: EconomicsRules) -> Economics:
    for name in ("lead_to_client_bp", "margin_bp", "ad_share_bp"):
        if not 1 <= getattr(economics, name) <= BP:
            raise EconomicsError(name)
    if not 0 <= economics.repeat_bp <= rules.max_repeat_bp:
        raise EconomicsError("repeat_bp")
    capacity = economics.capacity_per_week
    if capacity is not None and not 1 <= capacity <= rules.max_capacity_per_week:
        raise EconomicsError("capacity_per_week")
    return economics


def goal_micros(check_micros: int | None, economics: Economics) -> int | None:
    """Допустимая цена: чек × маржа × доля клиентов × (1 + повторные) × доля на рекламу."""
    if check_micros is None or check_micros <= 0:
        return None
    numerator = (
        check_micros
        * economics.margin_bp
        * economics.lead_to_client_bp
        * (BP + economics.repeat_bp)
        * economics.ad_share_bp
    )
    return numerator // BP**4
