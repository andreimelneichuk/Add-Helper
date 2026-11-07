import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from adhelper.budget.application.guard import WatchResult, WatchState, watch_once
from adhelper.budget.domain.balance import BalanceBaseline, BalanceState
from adhelper.budget.domain.ports import AlertSink, BudgetPlatform, PeriodRecord, PeriodStore
from adhelper.budget.domain.rules import BudgetRules
from adhelper.budget.domain.watch import SpendSnapshot, WatchAction, burn_from_history

logger = logging.getLogger(__name__)


def _state(record: PeriodRecord, snapshots: Sequence[SpendSnapshot]) -> WatchState:
    latest = snapshots[-1] if snapshots else None
    return WatchState(latest, burn_from_history(snapshots), record.stop_pending)


async def _publish(alerts: AlertSink, result: WatchResult, rules: BudgetRules) -> None:
    async def toggle(kind: str, active: bool) -> None:
        if active:
            details: dict[str, object] = {"rules_version": rules.version}
            if result.verdict is not None:
                details["projected_micros"] = result.verdict.projected
            await alerts.raise_alert(kind, details)
        else:
            await alerts.resolve(kind)

    await toggle("platform_unavailable", result.platform_unavailable)
    await toggle("budget_stop_pending", result.stop_pending)
    if result.stopped:
        await toggle("budget_stopped", True)
    if result.verdict is not None:
        await toggle("budget_near_limit", result.verdict.action is WatchAction.ALERT)
    if result.balance_state is not None:
        await toggle("zero_balance", result.balance_state is BalanceState.ZERO)
        if result.balance_state is BalanceState.MISMATCH:
            await toggle("balance_mismatch", True)
        if result.balance_state is BalanceState.UNAVAILABLE:
            if result.balance_auth_error:
                await toggle("platform_not_connected", True)
            else:
                await toggle("balance_unavailable", True)
        else:
            await toggle("balance_unavailable", False)
            if not result.balance_auth_error:
                await toggle("platform_not_connected", False)


async def _watch_period(
    record: PeriodRecord,
    store: PeriodStore,
    platform: BudgetPlatform,
    alerts: AlertSink,
    now: datetime,
    rules: BudgetRules,
) -> None:
    state = _state(record, await store.recent_snapshots(record.id))
    result = await watch_once(
        platform,
        record.account,
        record.period,
        state,
        now,
        rules,
        opening_balance=record.opening_balance,
        opening_spent=record.opening_spent or 0,
    )
    seconds = (
        result.verdict.next_check_seconds
        if result.verdict is not None and not result.stop_pending
        else rules.alert_check_interval_seconds
    )
    snapshot = result.state.snapshot if not result.platform_unavailable else None
    await store.save_cycle(
        record.id, snapshot, result.stop_pending, now + timedelta(seconds=seconds)
    )
    if result.balance is not None:
        await store.save_balance(record.id, result.balance, now, result.baseline)
    await _publish(alerts, result, rules)


async def run_watch_cycle(
    store: PeriodStore,
    platform_for: Callable[[UUID, str], BudgetPlatform | None],
    alerts_for: Callable[[UUID], AlertSink],
    now: datetime,
    rules_for: Callable[[str], BudgetRules],
) -> int:
    """Один проход стража. Сбой одного бизнеса не останавливает проверку остальных."""
    records = await store.due(now)
    for record in records:
        rules = rules_for(record.rules_version)
        retry_at = now + timedelta(seconds=rules.alert_check_interval_seconds)
        alerts = alerts_for(record.business_id)
        try:
            platform = platform_for(record.business_id, record.account)
            if platform is None:
                await alerts.raise_alert("platform_not_connected", {})
                await store.reschedule(record.id, retry_at)
                continue
            await _watch_period(record, store, platform, alerts, now, rules)
        except Exception as error:
            # В логе только тип: текст ошибки может содержать токен или адрес.
            logger.error("Страж бюджета: период %s, %s", record.id, type(error).__name__)
            await store.reschedule(record.id, retry_at)
    return len(records)


async def confirm_balance(
    store: PeriodStore, alerts: AlertSink, business_id: UUID, account: str, now: datetime
) -> bool:
    """Владелец подтвердил, что расхождение со счётом объяснимо: пополнение, кампания вне сервиса.

    Сверка начинается заново от последних прочитанных баланса и расхода. Кампании сами
    не включаются: это отдельное действие через журнал. Без прочитанных данных — отказ.
    """
    record = await store.active_for(business_id, account, now)
    if record is None or record.last_balance is None:
        return False
    spent = await store.latest_spent(record.id)
    if spent is None:
        return False
    checked_at = record.balance_checked_at or now
    await store.save_balance(
        record.id, record.last_balance, checked_at, BalanceBaseline(record.last_balance, spent)
    )
    await alerts.resolve("balance_mismatch")
    return True
