"""Мониторинг исходов: оценка последствий действий автопилота, автооткат, предохранители.

Откат идёт тем же единым путём, что и отмена владельцем (`rollback_change`): новая запись
журнала, политика, исполнитель, чтение результата. Оценку и решение об откате принимает код
по числам и версионируемым правилам, языковой модели здесь нет (docs/04-autopilot.md §6).
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from adhelper.autopilot.domain.explain import explain_rollback, explain_rollback_no_leads
from adhelper.autopilot.domain.outcomes import (
    ROLLBACK_STARTED,
    Measured,
    OutcomeResult,
    OutcomeRow,
    OutcomeRules,
    OutcomeVerdict,
    PendingOutcome,
    Period,
    evaluate_outcome,
)
from adhelper.autopilot.domain.safeguards import SafeguardState, check_safeguards
from adhelper.ledger.application.rollback import RollbackStatus, rollback_change
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import Change, ChangeStatus
from adhelper.ledger.domain.ports import GatewayUnavailable

DAY = timedelta(days=1)
ROLLED_BACK = "autopilot_rolled_back"
DEMOTED = "autopilot_demoted"
DIGEST = "autopilot_digest"


class OutcomeStore(Protocol):
    async def register_new(self, account: str, rules: OutcomeRules, now: datetime) -> int: ...
    async def claim_due(self, account: str, now: datetime) -> Sequence[PendingOutcome]: ...
    async def release(self, outcome_id: int, note: str) -> None: ...
    async def mark_rollback_started(self, outcome_id: int) -> None: ...
    async def measure(
        self, account: str, pending: PendingOutcome, rules: OutcomeRules, now: datetime
    ) -> Measured: ...
    async def save(
        self,
        outcome_id: int,
        *,
        status: str,
        verdict: OutcomeVerdict | None,
        cpl_change_bp: int | None,
        noise_bp: int | None,
        measured: Mapping[str, Any] | None,
        rollback_change_id: int | None,
        note: str,
        rules_version: str,
        at: datetime,
    ) -> bool: ...
    async def recent_verdicts(self, account: str, limit: int) -> Sequence[OutcomeVerdict]: ...
    async def rollbacks_since(self, account: str, since: datetime) -> int: ...
    async def data_untrusted(self) -> bool: ...
    async def autopilot_changes_since(self, account: str, since: datetime) -> int: ...
    async def evaluated_since(self, account: str, since: datetime) -> Sequence[OutcomeRow]: ...


class Notices(Protocol):
    async def notice(self, kind: str, key: str, details: Mapping[str, Any]) -> bool: ...


@dataclass
class MonitorResult:
    registered: int = 0
    evaluated: int = 0
    rolled_back: int = 0
    frozen: bool = False
    demoted_reason: str | None = None
    verdicts: list[OutcomeVerdict] = field(default_factory=list)


def _cpl(period: Period) -> int:
    return -(-period.spend // period.leads) if period.leads else 0


def _measured(before: Period, after: Period) -> dict[str, int]:
    return {
        "before_clicks": before.clicks,
        "before_spend": before.spend,
        "before_leads": before.leads,
        "after_clicks": after.clicks,
        "after_spend": after.spend,
        "after_leads": after.leads,
    }


class OutcomeMonitor:
    def __init__(
        self,
        store: OutcomeStore,
        ledger: Ledger,
        notices: Notices,
        rules: OutcomeRules,
        clock: Callable[[], datetime],
        demote: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._store = store
        self._ledger = ledger
        self._notices = notices
        self._rules = rules
        self._clock = clock
        self._demote = demote

    async def run(self, account: str) -> MonitorResult:
        now = self._clock()
        result = MonitorResult()
        # Тревога данных замораживает всё: по недостоверным цифрам не оцениваем и не откатываем.
        if await self._store.data_untrusted():
            result.frozen = True
            return result
        result.registered = await self._store.register_new(account, self._rules, now)
        for pending in await self._store.claim_due(account, now):
            await self._evaluate(account, pending, now, result)
            if result.demoted_reason is not None:
                break
        return result

    async def _evaluate(
        self, account: str, pending: PendingOutcome, now: datetime, result: MonitorResult
    ) -> None:
        rules = self._rules
        if pending.note == ROLLBACK_STARTED and await self._finish_rollback(
            account, pending, now, result
        ):
            return
        measured = await self._store.measure(account, pending, rules, now)
        if measured.reverted:
            await self._save(pending, "skipped", None, None, None, "уже отменено", now)
            return
        if measured.confounded:
            outcome = OutcomeResult(
                OutcomeVerdict.INSUFFICIENT, None, 0, "в окне менялось что-то ещё в кампании"
            )
        elif not measured.complete:
            if now < pending.evaluate_at + rules.max_wait_days * DAY:
                # Статистика за окно ещё не догружена: по неполным числам не судим.
                await self._store.release(pending.id, "ждём данные за окно")
                return
            outcome = OutcomeResult(OutcomeVerdict.INSUFFICIENT, None, 0, "не хватает данных")
        else:
            outcome = evaluate_outcome(measured.before, measured.after, rules)
        rollback_id: int | None = None
        note = outcome.reason
        if outcome.verdict is OutcomeVerdict.WORSE:
            done = await self._on_worse(account, pending, measured, outcome, now, result)
            if done is None:
                return  # откат ещё не доведён: исход остаётся в очереди и повторится
            rollback_id, note = done
        result.evaluated += 1
        result.verdicts.append(outcome.verdict)
        await self._save(pending, "evaluated", outcome, measured, rollback_id, note, now)

    async def _on_worse(
        self,
        account: str,
        pending: PendingOutcome,
        measured: Measured,
        outcome: OutcomeResult,
        now: datetime,
        result: MonitorResult,
    ) -> tuple[int | None, str] | None:
        rules = self._rules
        state = SafeguardState(
            rollbacks_in_window=await self._store.rollbacks_since(
                account, now - rules.rollback_window_days * DAY
            ),
            # Текущий исход «хуже» в базу ещё не записан, добавляем его к последним.
            recent_verdicts=[
                OutcomeVerdict.WORSE,
                *await self._store.recent_verdicts(account, rules.anomaly_window),
            ],
            data_untrusted=False,
        )
        decision = check_safeguards(state, rules)
        if decision.demote_reason is not None:
            result.demoted_reason = decision.demote_reason
            await self._demote(decision.demote_reason)
            # Письмо не зависит от результата: режим могли уже сменить, а письмо потерять.
            # Ключ один на исход, повтор после сбоя письмо не дублирует.
            await self._notices.notice(
                DEMOTED, f"demoted:{pending.change_id}", {"reason": decision.demote_reason}
            )
        if not decision.allow_rollback:
            return None, "хуже, но откат остановлен предохранителем"
        if outcome.cpl_change_bp is None:
            explanation = explain_rollback_no_leads(
                {
                    "after_spend_micros": measured.after.spend,
                    "before_cpl_micros": _cpl(measured.before),
                }
            )
        else:
            explanation = explain_rollback(
                {
                    "cpl_change_bp": outcome.cpl_change_bp,
                    "before_cpl_micros": _cpl(measured.before),
                    "after_cpl_micros": _cpl(measured.after),
                    "noise_bp": outcome.noise_bp,
                }
            )
        # Метка до отката: упавший воркер потом доведёт начатый откат, а не оценит заново.
        await self._store.mark_rollback_started(pending.id)
        rolled = await rollback_change(self._ledger, pending.change_id, explanation=explanation)
        if rolled.status is RollbackStatus.APPLIED:
            result.rolled_back += 1
            latest = await self._latest_rollback(account, pending)
            await self._notify_rolled_back(pending, explanation)
            return (latest.id if latest else None), "откат сделан"
        if rolled.status is RollbackStatus.PENDING:
            await self._store.release(pending.id, ROLLBACK_STARTED)
            return None
        return None, f"откат не выполнен: {rolled.status.value}"

    async def _latest_rollback(self, account: str, pending: PendingOutcome) -> Change | None:
        rows = await self._ledger.changes_on_target(account, pending.object_ref)
        reverts = [row for row in rows if row.reverts_change_id == pending.change_id]
        return max(reverts, key=lambda row: row.id, default=None)

    async def _notify_rolled_back(self, pending: PendingOutcome, explanation: str) -> None:
        await self._notices.notice(
            ROLLED_BACK,
            f"rolled_back:{pending.change_id}",
            {"explanation": explanation, "object_ref": pending.object_ref},
        )

    async def _finish_rollback(
        self, account: str, pending: PendingOutcome, now: datetime, result: MonitorResult
    ) -> bool:
        """Доводит начатый откат после падения воркера или ответа «выполняется».

        Истина — журнал: запись отката есть и применена — письмо (повтор не дублируется по
        ключу) и запись исхода; ещё не применена — ждём; записи нет — отката не было, оцениваем
        заново. Возвращает True, когда исход обработан.
        """
        revert = await self._latest_rollback(account, pending)
        if revert is None:
            return False
        if revert.status in {ChangeStatus.PLANNED, ChangeStatus.APPLYING}:
            with suppress(GatewayUnavailable):
                await self._ledger.execute_pending(account)
            current = await self._ledger.get_change(revert.id)
            revert = current or revert
        if revert.status in {ChangeStatus.PLANNED, ChangeStatus.APPLYING}:
            await self._store.release(pending.id, ROLLBACK_STARTED)
            return True
        if revert.status is ChangeStatus.APPLIED:
            result.rolled_back += 1
            await self._notify_rolled_back(pending, revert.explanation)
            note, rollback_id = "откат сделан", revert.id
        else:
            note, rollback_id = f"откат не выполнен: {revert.status.value}", None
        result.evaluated += 1
        result.verdicts.append(OutcomeVerdict.WORSE)
        measured = await self._store.measure(account, pending, self._rules, now)
        worse = OutcomeResult(OutcomeVerdict.WORSE, None, 0, note)
        await self._save(pending, "evaluated", worse, measured, rollback_id, note, now)
        return True

    async def _save(
        self,
        pending: PendingOutcome,
        status: str,
        outcome: OutcomeResult | None,
        measured: Measured | None,
        rollback_id: int | None,
        note: str,
        now: datetime,
    ) -> None:
        await self._store.save(
            pending.id,
            status=status,
            verdict=outcome.verdict if outcome else None,
            cpl_change_bp=outcome.cpl_change_bp if outcome else None,
            noise_bp=outcome.noise_bp if outcome else None,
            measured=_measured(measured.before, measured.after) if measured else None,
            rollback_change_id=rollback_id,
            note=note,
            rules_version=self._rules.version,
            at=now,
        )

    async def digest(self, account: str, *, always: bool) -> bool:
        """Сводка за сутки. Числа считает код из журнала и исходов."""
        now = self._clock()
        since = now - DAY
        done = await self._store.autopilot_changes_since(account, since)
        rows = await self._store.evaluated_since(account, since)
        verdicts = [row.verdict for row in rows]
        details = {
            "done": done,
            "better": verdicts.count(OutcomeVerdict.BETTER.value),
            "same": verdicts.count(OutcomeVerdict.SAME.value),
            "worse": verdicts.count(OutcomeVerdict.WORSE.value),
            "rolled_back": sum(row.rollback_change_id is not None for row in rows),
        }
        if not always and not any(details.values()):
            return False
        return await self._notices.notice(DIGEST, f"digest:{now.date().isoformat()}", details)
