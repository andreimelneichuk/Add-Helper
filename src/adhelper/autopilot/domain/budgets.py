"""Упирается ли кампания в свой лимит: docs/04-autopilot.md §4.

Лимит периода площадка расходует равномерно. Кампания, которая тратит не медленнее
равномерного темпа, сдерживается деньгами: ей есть куда расти. Кампания, которая тратит
заметно меньше, упирается в спрос, и рост лимита ей ничего не даст.
"""

from datetime import datetime

from adhelper.budget.domain.rules import BASIS_POINTS


def budget_limited(
    limit: int,
    spent: int,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
    pace_bp: int,
) -> bool:
    """Расход за прошедшую часть периода не ниже `pace_bp` от равномерного темпа.

    Неизвестный период, нулевой лимит и выключенный порог (0) дают «нет»: рост не планируется.
    """
    if pace_bp <= 0 or limit <= 0 or starts_at is None or ends_at is None:
        return False
    if spent >= limit:
        return True
    total = int((ends_at - starts_at).total_seconds())
    elapsed = min(int((now - starts_at).total_seconds()), total)
    if total <= 0 or elapsed <= 0:
        return False
    # spent / (limit × elapsed / total) ≥ pace_bp / 10000, без деления.
    return spent * total * BASIS_POINTS >= pace_bp * limit * elapsed
