from dataclasses import dataclass
from enum import StrEnum

from adhelper.budget.domain.rules import BudgetRules, floor_share


class BalanceState(StrEnum):
    OK = "ok"
    ZERO = "zero"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class BalanceBaseline:
    """Точка отсчёта сверки: баланс счёта и расход по статистике в один момент."""

    balance: int
    spent: int


def check_balance(
    opening_balance: int,
    current_balance: int,
    corrected_spent: int,
    hard_limit: int,
    rules: BudgetRules,
    opening_spent: int = 0,
) -> BalanceState:
    """Сверка со счётом: со счёта ушло заметно больше, чем показывает статистика.

    Сравниваем только то, что случилось после точки отсчёта: расход до неё не должен
    покрывать новую пропажу. Пополнения нам не видны, поэтому проверка односторонняя:
    пополнение может скрыть расхождение, но не создаёт ложной тревоги.
    """
    if current_balance <= 0:
        return BalanceState.ZERO
    left_account = opening_balance - current_balance
    tolerance = floor_share(hard_limit, rules.balance_tolerance_bp)
    if left_account > corrected_spent - opening_spent + tolerance:
        return BalanceState.MISMATCH
    return BalanceState.OK
