from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from adhelper.budget.domain.balance import BalanceBaseline, BalanceState, check_balance
from adhelper.budget.domain.limits import (
    BudgetPeriod,
    LimitChange,
    LimitDecision,
    check_limit_change,
)
from adhelper.budget.domain.ports import BudgetPlatform, PlatformUnavailable
from adhelper.budget.domain.rules import BudgetRules
from adhelper.budget.domain.transfer import TransferPlan, next_step
from adhelper.budget.domain.watch import (
    BurnRate,
    SpendSnapshot,
    WatchAction,
    WatchVerdict,
    corrected_spend,
    evaluate_watch,
)

# Перенос занимает два шага; запас нужен на случай, если площадка молча не применила лимит.
MAX_TRANSFER_ITERATIONS = 4


class ChangeStatus(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    REJECTED = "rejected"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class ChangeOutcome:
    status: ChangeStatus
    decision: LimitDecision | None = None


async def check_change(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    change: LimitChange,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
    opening_spent: int = 0,
) -> LimitDecision:
    campaigns = await platform.read_campaigns(account)
    snapshot = await platform.fetch_spend(account)
    spent = corrected_spend(snapshot, burn, now)
    period_spent = max(spent - opening_spent, 0)
    return check_limit_change(period, campaigns, change, period_spent, now, rules)


async def _read_limits(platform: BudgetPlatform, account: str) -> dict[str, int]:
    return {c.campaign_id: c.limit for c in await platform.read_campaigns(account)}


async def apply_limit_change(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    change: LimitChange,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
    opening_spent: int = 0,
) -> ChangeOutcome:
    """Повтор безопасен: сначала читаем факт и сравниваем его с целью."""
    try:
        if (await _read_limits(platform, account)).get(change.campaign_id) == change.new_limit:
            return ChangeOutcome(ChangeStatus.ALREADY_APPLIED)
        decision = await check_change(
            platform, account, period, change, burn, now, rules, opening_spent=opening_spent
        )
        if not decision.allowed:
            await platform.record_rejection(account, change, decision)
            return ChangeOutcome(ChangeStatus.REJECTED, decision)
        await platform.set_limit(account, change)
        applied = (await _read_limits(platform, account)).get(
            change.campaign_id
        ) == change.new_limit
    except PlatformUnavailable:
        return ChangeOutcome(ChangeStatus.UNCONFIRMED)
    return ChangeOutcome(ChangeStatus.APPLIED if applied else ChangeStatus.UNCONFIRMED, decision)


class TransferStatus(StrEnum):
    DONE = "done"
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"
    STUCK = "stuck"


async def drive_transfer(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    plan: TransferPlan,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
) -> TransferStatus:
    """Доводит перенос до цели по фактическому состоянию; можно запускать повторно после сбоя."""
    try:
        for _ in range(MAX_TRANSFER_ITERATIONS):
            step = next_step(plan, await _read_limits(platform, account))
            if step is None:
                return TransferStatus.DONE
            decision = await check_change(platform, account, period, step, burn, now, rules)
            if not decision.allowed:
                await platform.record_rejection(account, step, decision)
                return TransferStatus.REJECTED
            await platform.set_limit(account, step)
    except PlatformUnavailable:
        return TransferStatus.INTERRUPTED
    return TransferStatus.STUCK


@dataclass(frozen=True)
class WatchState:
    snapshot: SpendSnapshot | None
    burn: BurnRate
    stop_pending: bool

    @staticmethod
    def initial() -> WatchState:
        return WatchState(snapshot=None, burn=BurnRate.zero(), stop_pending=False)


@dataclass(frozen=True)
class WatchResult:
    state: WatchState
    verdict: WatchVerdict | None = None
    stopped: bool = False
    stop_pending: bool = False
    platform_unavailable: bool = False
    balance_state: BalanceState | None = None
    # Прочитанный баланс счёта; None — не читали или площадка не ответила.
    balance: int | None = None
    # Новая точка отсчёта сверки, если её ещё не было.
    baseline: BalanceBaseline | None = None


async def _stop_all(platform: BudgetPlatform, account: str) -> bool:
    """Останавливает активные кампании и подтверждает это чтением."""
    try:
        active = [c.campaign_id for c in await platform.read_campaigns(account) if c.active]
        if active:
            await platform.pause_campaigns(account, active)
        return not any(c.active for c in await platform.read_campaigns(account))
    except PlatformUnavailable:
        return False


@dataclass(frozen=True)
class _BalanceReading:
    balance: int | None = None
    state: BalanceState | None = None
    baseline: BalanceBaseline | None = None


async def _read_balance(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    opening: BalanceBaseline | None,
    snapshot: SpendSnapshot,
    spent: int,
    rules: BudgetRules,
) -> _BalanceReading:
    try:
        current = await platform.fetch_balance(account)
    except PlatformUnavailable:
        return _BalanceReading()
    if opening is None:
        # Без точки отсчёта сверять не с чем: ставим её сейчас, а не пропускаем сверку молча.
        baseline = BalanceBaseline(current, snapshot.reported)
        state = BalanceState.ZERO if current <= 0 else BalanceState.OK
        return _BalanceReading(current, state, baseline)
    state = check_balance(
        opening.balance, current, spent, period.hard_limit, rules, opening_spent=opening.spent
    )
    return _BalanceReading(current, state)


async def watch_once(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    state: WatchState,
    now: datetime,
    rules: BudgetRules,
    opening_balance: int | None = None,
    opening_spent: int = 0,
) -> WatchResult:
    try:
        snapshot = await platform.fetch_spend(account)
    except PlatformUnavailable:
        # Решение об остановке не отменяется из-за того, что статистика недоступна.
        stopped = state.stop_pending and await _stop_all(platform, account)
        new_state = WatchState(state.snapshot, state.burn, state.stop_pending and not stopped)
        return WatchResult(
            state=new_state,
            stopped=stopped,
            stop_pending=new_state.stop_pending,
            platform_unavailable=True,
        )
    # Площадка вернула более старые данные, чем мы видели: прежние свежее и не меньше.
    if state.snapshot is not None and snapshot.data_as_of < state.snapshot.data_as_of:
        snapshot = state.snapshot
    burn = BurnRate.between(state.snapshot, snapshot, state.burn) if state.snapshot else state.burn
    verdict = evaluate_watch(period, snapshot, burn, now, rules)
    opening = (
        BalanceBaseline(opening_balance, opening_spent) if opening_balance is not None else None
    )
    reading = await _read_balance(
        platform, account, period, opening, snapshot, corrected_spend(snapshot, burn, now), rules
    )
    balance_state = reading.state
    must_stop = (
        state.stop_pending
        or verdict.action is WatchAction.STOP
        or balance_state is BalanceState.MISMATCH
    )
    stopped = must_stop and await _stop_all(platform, account)
    pending = must_stop and not stopped
    return WatchResult(
        state=WatchState(snapshot, burn, pending),
        verdict=verdict,
        stopped=stopped,
        stop_pending=pending,
        balance_state=balance_state,
        balance=reading.balance,
        baseline=reading.baseline,
    )
