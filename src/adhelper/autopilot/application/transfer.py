"""Перенос лимита между кампаниями как действие автопилота.

Порядок: политика → план → журнал со снимком «до» → исполнитель → чтение результата. Платформа
здесь всегда журналируемая (`LedgeredBudgetPlatform`): прямого пути на площадку у переноса нет.
Исполнить перенос можно только по `AuthorizedTransfer`, а его выдаёт только `authorize_transfer`
после проверки политики, поэтому политику нельзя обойти.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from adhelper.autopilot.domain.policy import (
    PolicyAction,
    PolicyDecision,
    PolicyInput,
    PolicyReason,
    PolicyRules,
    check_policy,
)
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


@dataclass(frozen=True)
class AuthorizedTransfer:
    """План, прошедший политику. Создаётся только в `authorize_transfer`."""

    plan: TransferPlan
    decision: PolicyDecision
    amount: int


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
    return TransferOutcome(
        TransferResult.AUTHORIZED, first, AuthorizedTransfer(plan, first, amount)
    )


async def resume_transfer(
    platform: BudgetPlatform,
    account: str,
    period: BudgetPeriod,
    authorized: AuthorizedTransfer,
    burn: BurnRate,
    now: datetime,
    rules: BudgetRules,
) -> TransferStatus:
    """Доводит разрешённый перенос до цели; повтор после сбоя начинается с чтения лимитов.

    Политику повторно не спрашиваем: перенос уже разрешён, а незавершённый (донор снижен,
    получатель нет) только держит бюджет с запасом. Каждый шаг по-прежнему проходит страж бюджета.
    """
    return await drive_transfer(platform, account, period, authorized.plan, burn, now, rules)


__all__ = [
    "AuthorizedTransfer",
    "CampaignTraits",
    "PolicyFacts",
    "PolicyReason",
    "TransferOutcome",
    "TransferResult",
    "authorize_transfer",
    "resume_transfer",
]
