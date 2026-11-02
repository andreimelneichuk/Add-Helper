"""План бюджета до запуска: сумма в месяц, которую владелец выбрал в мастере.

План не трогает площадку. Лимиты в Директе ставит запуск (T6) по этому плану, через журнал.
"""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class PlanRules:
    version: str
    min_monthly_rubles: int
    max_monthly_rubles: int
    presets: tuple[int, ...]


class PlanProblem(StrEnum):
    BELOW_MIN = "below_min"
    ABOVE_MAX = "above_max"


def check_plan(rubles: int, rules: PlanRules) -> PlanProblem | None:
    if rubles < rules.min_monthly_rubles:
        return PlanProblem.BELOW_MIN
    if rubles > rules.max_monthly_rubles:
        return PlanProblem.ABOVE_MAX
    return None
