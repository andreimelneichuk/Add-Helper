"""Предохранители автопилота (docs/04-autopilot.md §6): когда стоп важнее оптимизации.

Решает код по числам: тревога данных замораживает оценку, частые откаты и серия плохих
исходов выключают самостоятельный режим. Оценка исхода и откат остаются отдельно.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from adhelper.autopilot.domain.outcomes import OutcomeRules, OutcomeVerdict


@dataclass(frozen=True)
class SafeguardState:
    rollbacks_in_window: int
    # Вердикты последних оценённых исходов, новые первыми.
    recent_verdicts: Sequence[OutcomeVerdict]
    data_untrusted: bool


@dataclass(frozen=True)
class SafeguardDecision:
    allow_rollback: bool
    frozen: bool
    demote_reason: str | None


def check_safeguards(state: SafeguardState, rules: OutcomeRules) -> SafeguardDecision:
    if state.data_untrusted:
        # Данным не верим: ни оценивать, ни откатывать по ним нельзя.
        return SafeguardDecision(allow_rollback=False, frozen=True, demote_reason=None)
    done = state.rollbacks_in_window
    if done >= rules.max_rollbacks:
        # Лимит уже исчерпан: откатов больше не делаем, решает владелец.
        return SafeguardDecision(
            allow_rollback=False,
            frozen=False,
            demote_reason=(f"автоматических откатов за {rules.rollback_window_days} дн.: {done}"),
        )
    judged = [v for v in state.recent_verdicts if v is not OutcomeVerdict.INSUFFICIENT]
    worse = sum(v is OutcomeVerdict.WORSE for v in judged[: rules.anomaly_window])
    if worse >= rules.anomaly_worse_count:
        return SafeguardDecision(
            allow_rollback=True,
            frozen=False,
            demote_reason=(
                f"из последних {min(len(judged), rules.anomaly_window)} оценённых действий "
                f"хуже стало в {worse}"
            ),
        )
    if done + 1 >= rules.max_rollbacks:
        # Этот откат — последний допустимый: делаем его и возвращаем «Предлагает».
        return SafeguardDecision(
            allow_rollback=True,
            frozen=False,
            demote_reason=(
                f"автоматических откатов за {rules.rollback_window_days} дн.: {done + 1}"
            ),
        )
    return SafeguardDecision(allow_rollback=True, frozen=False, demote_reason=None)
