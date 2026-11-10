"""Рост лимита эффективной кампании в режиме «Сам». docs/04-autopilot.md §4.

Сначала свободные деньги: если сумма лимитов ниже рабочего бюджета, лимит растёт одним шагом
через стража бюджета и журнал. Свободных денег нет — перенос с кампании без эффективных
объявлений: политика, запись намерения, снижение донора, потом рост получателя. Всё, что не
получилось сделать безопасно, уходит владельцу карточкой: площадка остаётся как была.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

from adhelper.autopilot.application.transfer import (
    CampaignTraits,
    PolicyFacts,
    TransferPlatform,
    TransferResult,
    authorize_transfer,
    run_transfer,
)
from adhelper.autopilot.domain.policy import GateFacts, PolicyAction, PolicyRules
from adhelper.autopilot.domain.transfers import TransferStore
from adhelper.budget.application.guard import ChangeStatus, TransferStatus, apply_limit_change
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, LimitChange
from adhelper.budget.domain.ports import CampaignStrategy, PlatformUnavailable
from adhelper.budget.domain.rules import BudgetRules
from adhelper.budget.domain.watch import BurnRate

logger = logging.getLogger(__name__)

EXECUTED = "executed"
PENDING = "pending"
# Сам не смог: решение остаётся за владельцем, как в режиме «Предлагает».
CARD = "awaiting_confirmation"


def increase_facts(gate: GateFacts) -> PolicyFacts:
    """Факты политики для переноса: ворота уже пропустили рост лимита этого кабинета."""
    return PolicyFacts(
        allowed=frozenset({PolicyAction.INCREASE_LIMIT}),
        changes_today=gate.changes_today,
        moved_this_week=gate.moved_this_week,
        data_alert=gate.data_alert,
        api_points_rest=gate.api_points_rest,
        last_limit_change=gate.last_limit_change,
    )


def strategy_traits(strategies: Mapping[str, CampaignStrategy]) -> dict[str, CampaignTraits]:
    return {cid: CampaignTraits(s.auto, s.learns) for cid, s in strategies.items()}


def _donor(
    campaigns: Mapping[str, CampaignBudget], donors: Sequence[str], amount: int
) -> str | None:
    """Донор с наибольшим неизрасходованным лимитом, которого хватает на перенос."""
    fitting = [
        campaigns[cid]
        for cid in donors
        if cid in campaigns
        and campaigns[cid].active
        and campaigns[cid].limit - campaigns[cid].spent >= amount
    ]
    if not fitting:
        return None
    return max(fitting, key=lambda c: (c.limit - c.spent, c.campaign_id)).campaign_id


async def raise_limit(
    platform: TransferPlatform,
    store: TransferStore,
    account: str,
    period: BudgetPeriod,
    recipient_id: str,
    new_limit: int,
    donors: Sequence[str],
    facts: PolicyFacts,
    traits: Mapping[str, CampaignTraits],
    burn: BurnRate,
    now: datetime,
    budget_rules: BudgetRules,
    policy: PolicyRules,
) -> str:
    """Статус для журнала решений: `executed`, `pending` или карточка владельцу."""
    try:
        campaigns = {c.campaign_id: c for c in await platform.read_campaigns(account)}
    except PlatformUnavailable:
        return CARD
    recipient = campaigns.get(recipient_id)
    if recipient is None:
        return CARD
    amount = new_limit - recipient.limit
    if amount <= 0:
        return EXECUTED
    free = period.operating_budget - sum(c.limit for c in campaigns.values())
    if free >= amount:
        outcome = await apply_limit_change(
            platform, account, period, LimitChange(recipient_id, new_limit), burn, now, budget_rules
        )
        if outcome.status in (ChangeStatus.APPLIED, ChangeStatus.ALREADY_APPLIED):
            return EXECUTED
        if outcome.status is ChangeStatus.UNCONFIRMED:
            return PENDING
        return CARD
    donor = _donor(campaigns, [d for d in donors if d != recipient_id], amount)
    if donor is None:
        return CARD
    authorized = await authorize_transfer(
        platform, account, period, donor, recipient_id, amount, facts, traits, now, policy
    )
    if authorized.result is not TransferResult.AUTHORIZED or authorized.authorized is None:
        reason = authorized.policy.reason if authorized.policy else authorized.result
        logger.info("Перенос лимита не разрешён: %s", reason)
        return CARD
    _, status = await run_transfer(
        store, platform, account, period, authorized.authorized, burn, now, budget_rules, policy
    )
    if status is TransferStatus.DONE:
        return EXECUTED
    if status is TransferStatus.INTERRUPTED:
        # Намерение записано: часовой доводчик закончит перенос или откатит его.
        return PENDING
    return CARD
