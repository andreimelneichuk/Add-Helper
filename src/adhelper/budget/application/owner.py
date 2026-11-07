"""Решения владельца о показах и бюджете. Площадку меняет только журнал."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from adhelper.budget.domain.owner import plan_budget_change
from adhelper.budget.domain.ports import BudgetPlatform, PeriodStore, PlatformUnavailable
from adhelper.budget.domain.watch import burn_from_history, corrected_spend, freshest
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import (
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)
from adhelper.ledger.domain.ports import GatewayUnavailable

NOT_OWNER_PAUSE = "not_owner_pause"
NO_ANSWER = "platform_unavailable"


@dataclass
class ResumeReport:
    resumed: list[str] = field(default_factory=list)
    # Отказ по правилам: пауза не владельца или бюджет не выдержит.
    refused: list[tuple[str, str]] = field(default_factory=list)
    # Площадка не ответила или не применила.
    failed: list[tuple[str, str]] = field(default_factory=list)


async def _paused_by_owner(ledger: Ledger, account: str, campaign_id: str) -> bool:
    """Последнее применённое включение или выключение кампании сделал владелец паузой."""
    rows = await ledger.changes_on_target(account, campaign_id)
    toggles = [
        r
        for r in rows
        if r.status is ChangeStatus.APPLIED and r.kind in {ChangeKind.PAUSE, ChangeKind.RESUME}
    ]
    if not toggles:
        return False
    last = max(toggles, key=lambda r: r.id)
    return last.kind is ChangeKind.PAUSE and last.source is ChangeSource.USER


async def resume_account(
    account: str,
    platform: BudgetPlatform,
    ledger: Ledger,
    report: ResumeReport,
    explanation: str,
    key_prefix: str,
) -> None:
    """Снимает только паузу владельца. Остановку стражем бюджета кнопка не снимает."""
    try:
        campaigns = await platform.read_campaigns(account)
    except PlatformUnavailable:
        report.failed.append((account, NO_ANSWER))
        return
    for campaign in campaigns:
        if campaign.active:
            continue
        ref = f"{account}:{campaign.campaign_id}"
        if not await _paused_by_owner(ledger, account, campaign.campaign_id):
            report.refused.append((ref, NOT_OWNER_PAUSE))
            continue
        try:
            # Включаем по одной: каждая следующая проверяется с учётом уже включённых.
            submitted = await ledger.submit(
                ProposedChange(
                    account=account,
                    source=ChangeSource.USER,
                    kind=ChangeKind.RESUME,
                    target_ref=campaign.campaign_id,
                    planned={"active": True},
                    explanation=explanation,
                    idempotency_key=f"{key_prefix}:{campaign.campaign_id}",
                )
            )
            await ledger.execute_pending(account)
        except GatewayUnavailable:
            report.failed.append((ref, NO_ANSWER))
            continue
        change = await ledger.get_change(submitted.id)
        if change is not None and change.status is ChangeStatus.APPLIED:
            report.resumed.append(ref)
        elif change is not None and change.status is ChangeStatus.FAILED:
            report.refused.append((ref, change.failure_reason or "rejected"))
        else:
            report.failed.append((ref, NO_ANSWER))


class BudgetStatus(StrEnum):
    APPLIED = "applied"
    BELOW_SPENT = "below_spent"
    NO_PERIOD = "no_period"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class BudgetResult:
    status: BudgetStatus
    minimum: int | None = None
    cuts: int = 0


async def change_budget(
    store: PeriodStore,
    platform: BudgetPlatform,
    ledger: Ledger,
    business_id: UUID,
    account: str,
    new_hard_limit: int,
    now: datetime,
) -> BudgetResult:
    """Сначала снижаем лимиты на площадке и подтверждаем чтением, только потом меняем бюджет.

    Если сервис остановится посередине, на площадке останутся лимиты не выше прежних,
    а сохранённый бюджет прежний: сумма лимитов в его рамках в любой момент.
    """
    record = await store.active_for(business_id, account, now)
    if record is None:
        return BudgetResult(BudgetStatus.NO_PERIOD)
    history = await store.recent_snapshots(record.id)
    burn = burn_from_history(history)
    try:
        campaigns = await platform.read_campaigns(account)
        snapshot = await platform.fetch_spend(account)
    except PlatformUnavailable:
        return BudgetResult(BudgetStatus.UNCONFIRMED)
    spent = corrected_spend(freshest(snapshot, history), burn, now)
    period_spent = max(spent - (record.opening_spent or 0), 0)
    plan = plan_budget_change(
        record.period,
        campaigns,
        new_hard_limit,
        period_spent,
    )
    if plan.refused:
        return BudgetResult(BudgetStatus.BELOW_SPENT, minimum=plan.minimum)
    if plan.cuts:
        try:
            for cut in plan.cuts:
                await ledger.submit(
                    ProposedChange(
                        account=account,
                        source=ChangeSource.USER,
                        kind=ChangeKind.SET_LIMIT,
                        target_ref=cut.campaign_id,
                        planned={"limit": cut.new_limit},
                        explanation=(
                            f"Снизил лимит кампании {cut.campaign_id}: вы уменьшили бюджет месяца."
                        ),
                        idempotency_key=f"set_limit:{cut.campaign_id}:{cut.new_limit}",
                    )
                )
            await ledger.execute_pending(account)
            limits = {c.campaign_id: c.limit for c in await platform.read_campaigns(account)}
        except (GatewayUnavailable, PlatformUnavailable):
            return BudgetResult(BudgetStatus.UNCONFIRMED, cuts=len(plan.cuts))
        if any(limits.get(cut.campaign_id, 0) > cut.new_limit for cut in plan.cuts):
            return BudgetResult(BudgetStatus.UNCONFIRMED, cuts=len(plan.cuts))
    await store.set_hard_limit(record.id, new_hard_limit)
    return BudgetResult(BudgetStatus.APPLIED, cuts=len(plan.cuts))
