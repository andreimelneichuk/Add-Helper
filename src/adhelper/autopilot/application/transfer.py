"""Перенос лимита между кампаниями как действие автопилота.

Порядок: политика → план → журнал со снимком «до» → исполнитель → чтение результата. Платформа
здесь всегда журналируемая (`LedgeredBudgetPlatform`): прямого пути на площадку у переноса нет.
Исполнить перенос можно только по `AuthorizedTransfer`: собрать его вне этого модуля нельзя
(конструктор требует закрытый жетон), его выдают `authorize_transfer` и `restore_transfer`, а
`drive_transfer` стража бюджета вызывается только отсюда (это проверяет архитектурный тест).
Намерение переноса пишется в базу до первого изменения, поэтому недоведённый перенос
доводится или откатывается после остановки сервиса.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from adhelper.autopilot.domain.policy import (
    PolicyAction,
    PolicyDecision,
    PolicyInput,
    PolicyReason,
    PolicyRules,
    check_policy,
)
from adhelper.autopilot.domain.transfers import TransferRecord, TransferState, TransferStore
from adhelper.budget.application.guard import TransferStatus, drive_transfer
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.ports import BudgetPlatform, PlatformUnavailable
from adhelper.budget.domain.rules import BudgetRules
from adhelper.budget.domain.transfer import TransferPlan, TransferRejected, plan_transfer
from adhelper.budget.domain.watch import BurnRate


@dataclass(frozen=True)
class CampaignTraits:
    """Что известно о кампании для политики; None значит «не знаем» и трактуется как опасное."""

    auto_strategy: bool | None
    in_learning: bool | None


@dataclass(frozen=True)
class PolicyFacts:
    """Состояние кабинета, общее для всех действий цикла."""

    allowed: frozenset[PolicyAction]
    changes_today: int
    moved_this_week: int
    data_alert: bool
    api_points_rest: int | None


class TransferResult(StrEnum):
    AUTHORIZED = "authorized"
    DENIED = "denied"
    REJECTED = "rejected"
    UNKNOWN_CAMPAIGN = "unknown_campaign"
    UNAVAILABLE = "unavailable"


_ISSUE = object()


@dataclass(frozen=True)
class AuthorizedTransfer:
    """План, прошедший политику. Без жетона модуля собрать его нельзя."""

    plan: TransferPlan
    decision: PolicyDecision
    amount: int
    _issued: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issued is not _ISSUE:
            raise TypeError("AuthorizedTransfer выдаёт только политика переноса")
        if not self.decision.allowed:
            raise ValueError("Перенос без разрешения политики")


def _issue(plan: TransferPlan, decision: PolicyDecision, amount: int) -> AuthorizedTransfer:
    return AuthorizedTransfer(plan, decision, amount, _ISSUE)


@dataclass(frozen=True)
class TransferOutcome:
    result: TransferResult
    policy: PolicyDecision | None = None
    authorized: AuthorizedTransfer | None = None
    reason: str | None = None


async def authorize_transfer(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    donor_id: str,
    recipient_id: str,
    amount: int,
    facts: PolicyFacts,
    traits: Mapping[str, CampaignTraits],
    now: datetime,
    rules: PolicyRules,
) -> TransferOutcome:
    """Читает кабинет, проверяет политику для обеих кампаний и строит план. Ничего не меняет."""
    try:
        campaigns = {c.campaign_id: c for c in await platform.read_campaigns(account)}
    except PlatformUnavailable:
        return TransferOutcome(TransferResult.UNAVAILABLE)
    donor, recipient = campaigns.get(donor_id), campaigns.get(recipient_id)
    if donor is None or recipient is None:
        return TransferOutcome(TransferResult.UNKNOWN_CAMPAIGN)
    first: PolicyDecision | None = None
    for campaign in (recipient, donor):
        known = traits.get(campaign.campaign_id, CampaignTraits(None, None))
        decision = check_policy(
            PolicyInput(
                action=PolicyAction.TRANSFER_LIMIT,
                ref=campaign.campaign_id,
                campaign_id=campaign.campaign_id,
                allowed=facts.allowed,
                now=now,
                changes_today=facts.changes_today,
                moved_this_week=facts.moved_this_week,
                amount=amount,
                operating_budget=period.operating_budget,
                active_ads_in_group=None,
                ad_in_learning=False,
                campaign_in_learning=known.in_learning,
                auto_strategy=known.auto_strategy,
                last_limit_change_at=campaign.last_increase_at,
                data_alert=facts.data_alert,
                api_points_rest=facts.api_points_rest,
            ),
            rules,
        )
        if not decision.allowed:
            return TransferOutcome(TransferResult.DENIED, decision)
        first = first or decision
    try:
        plan = plan_transfer(donor, recipient, amount)
    except TransferRejected as error:
        return TransferOutcome(TransferResult.REJECTED, first, reason=str(error))
    if first is None:
        raise AssertionError("Политика не вернула решения")
    return TransferOutcome(TransferResult.AUTHORIZED, first, _issue(plan, first, amount))


async def resume_transfer(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    authorized: AuthorizedTransfer,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
    policy: PolicyRules,
) -> TransferStatus:
    """Доводит разрешённый перенос до цели; повтор после сбоя начинается с чтения лимитов.

    Разрешение должно быть действующим: решение допускающее и принято по версии правил
    политики, с которой работает цикл. Иначе ничего не трогаем. Каждый шаг по-прежнему
    проходит страж бюджета.
    """
    if not authorized.decision.allowed or authorized.decision.rules_version != policy.version:
        return TransferStatus.REJECTED
    return await drive_transfer(platform, account, period, authorized.plan, burn, now, rules)


class TransferPlatform(BudgetPlatform, Protocol):
    """Журналируемая площадка, умеющая писать лимиты под идентификатором переноса."""

    def for_transfer(self, transfer_id: str) -> BudgetPlatform: ...


_STATE = {
    TransferStatus.DONE: TransferState.DONE,
    TransferStatus.REJECTED: TransferState.REJECTED,
    TransferStatus.ROLLED_BACK: TransferState.ROLLED_BACK,
    TransferStatus.STUCK: TransferState.STUCK,
    TransferStatus.INTERRUPTED: TransferState.OPEN,
}


def _record(transfer_id: str, account: str, authorized: AuthorizedTransfer) -> TransferRecord:
    plan = authorized.plan
    if plan.donor_start is None:
        raise ValueError("В плане нет исходного лимита донора")
    return TransferRecord(
        id=transfer_id,
        account=account,
        donor_id=plan.donor_id,
        recipient_id=plan.recipient_id,
        donor_start=plan.donor_start,
        donor_target=plan.donor_target,
        recipient_target=plan.recipient_target,
        amount=authorized.amount,
        decision={
            "reason": None,
            "rules_version": authorized.decision.rules_version,
            "inputs": authorized.decision.inputs,
        },
    )


def restore_transfer(record: TransferRecord) -> AuthorizedTransfer:
    """Разрешение из записи: берём то, что решила политика, а не пересобираем план."""
    decision = PolicyDecision(
        reason=None,
        rules_version=str(record.decision["rules_version"]),
        inputs=dict(record.decision.get("inputs", {})),
    )
    plan = TransferPlan(
        record.donor_id,
        record.recipient_id,
        record.donor_target,
        record.recipient_target,
        record.donor_start,
    )
    return _issue(plan, decision, record.amount)


async def run_transfer(
    store: TransferStore,
    platform: TransferPlatform,
    account: str,
    period: BudgetPeriod,
    authorized: AuthorizedTransfer,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
    policy: PolicyRules,
) -> tuple[str, TransferStatus]:
    """Записывает намерение, затем доводит перенос; все лимиты идут под одним идентификатором."""
    transfer_id = str(uuid.uuid4())
    await store.add(_record(transfer_id, account, authorized))
    status = await resume_transfer(
        platform.for_transfer(transfer_id), account, period, authorized, burn, now, rules, policy
    )
    await store.set_state(transfer_id, _STATE[status])
    return transfer_id, status


async def finish_open_transfers(
    store: TransferStore,
    platform: TransferPlatform,
    period: BudgetPeriod,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
    policy: PolicyRules,
) -> dict[str, TransferStatus]:
    """После сбоя: открытый перенос доводим, а если разрешение устарело, откатываем."""
    outcome: dict[str, TransferStatus] = {}
    for record in await store.open_transfers():
        authorized = restore_transfer(record)
        if authorized.decision.rules_version != policy.version:
            outcome[record.id] = await _cancel(store, platform, period, record, burn, now, rules)
            continue
        status = await resume_transfer(
            platform.for_transfer(record.id),
            record.account,
            period,
            authorized,
            burn,
            now,
            rules,
            policy,
        )
        await store.set_state(record.id, _STATE[status])
        outcome[record.id] = status
    return outcome


async def _cancel(
    store: TransferStore,
    platform: TransferPlatform,
    period: BudgetPeriod,
    record: TransferRecord,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
) -> TransferStatus:
    """Обратный перенос: получатель снижается первым, донор растёт после, и тоже под стражем."""
    reverse = TransferPlan(
        donor_id=record.recipient_id,
        recipient_id=record.donor_id,
        donor_target=record.recipient_start,
        recipient_target=record.donor_start,
        donor_start=record.recipient_target,
    )
    status = await drive_transfer(
        platform.for_transfer(record.id), record.account, period, reverse, burn, now, rules
    )
    if status is TransferStatus.DONE:
        await store.set_state(record.id, TransferState.CANCELLED)
    elif status is TransferStatus.INTERRUPTED:
        await store.set_state(record.id, TransferState.OPEN)
    else:
        await store.set_state(record.id, TransferState.STUCK)
    return status


async def cancel_transfer(
    store: TransferStore,
    platform: TransferPlatform,
    transfer_id: str,
    period: BudgetPeriod,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
) -> TransferStatus | None:
    """«Отменить перенос»: сначала получатель, затем донор. None — такого переноса нет."""
    record = await store.get(transfer_id)
    if record is None or record.state in {
        TransferState.ROLLED_BACK,
        TransferState.REJECTED,
        TransferState.CANCELLED,
    }:
        return None
    return await _cancel(store, platform, period, record, burn, now, rules)


__all__ = [
    "AuthorizedTransfer",
    "CampaignTraits",
    "PolicyFacts",
    "PolicyReason",
    "TransferOutcome",
    "TransferPlatform",
    "TransferResult",
    "authorize_transfer",
    "cancel_transfer",
    "finish_open_transfers",
    "restore_transfer",
    "resume_transfer",
    "run_transfer",
]
