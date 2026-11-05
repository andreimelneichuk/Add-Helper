"""Решения владельца о деньгах: снять свою паузу и сменить бюджет месяца.

Оба решения детерминированы и проверяют худший случай так же, как рост лимита.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from adhelper.budget.domain.limits import (
    BudgetPeriod,
    CampaignBudget,
    LimitChange,
    RejectionReason,
)
from adhelper.budget.domain.rules import BASIS_POINTS


def check_resume(
    period: BudgetPeriod,
    campaigns: Sequence[CampaignBudget],
    campaign_id: str,
    corrected_spent: int,
    now: datetime,
) -> RejectionReason | None:
    """Включённая кампания может потратить весь остаток лимита: он должен помещаться в бюджет."""
    target = next((c for c in campaigns if c.campaign_id == campaign_id), None)
    if target is None:
        return RejectionReason.UNKNOWN_CAMPAIGN
    if period.ends_at is not None and now >= period.ends_at:
        return RejectionReason.PERIOD_ENDED
    if sum(c.limit for c in campaigns) > period.operating_budget:
        return RejectionReason.EXCEEDS_OPERATING_BUDGET
    worst = corrected_spent + sum(
        max(c.limit - c.spent, 0) for c in campaigns if c.active or c.campaign_id == campaign_id
    )
    if worst > period.hard_limit:
        return RejectionReason.WORST_CASE_EXCEEDS_HARD_LIMIT
    return None


def minimum_budget(spent: int, reserve_bp: int) -> int:
    """Наименьший бюджет, рабочая часть которого покрывает уже потраченное."""
    if reserve_bp >= BASIS_POINTS:
        raise ValueError("Резерв в 100% не оставляет рабочего бюджета")
    return -(-spent * BASIS_POINTS // (BASIS_POINTS - reserve_bp))


@dataclass(frozen=True)
class BudgetChangePlan:
    period: BudgetPeriod
    # Снижения лимитов, которые должны примениться на площадке до смены бюджета.
    cuts: tuple[LimitChange, ...]
    refused: bool
    minimum: int


def plan_budget_change(
    period: BudgetPeriod,
    campaigns: Sequence[CampaignBudget],
    new_hard_limit: int,
    corrected_spent: int,
) -> BudgetChangePlan:
    """Новый бюджет не ниже потраченного; при снижении лимиты урезаются пропорционально остатку."""
    new = replace(period, hard_limit=new_hard_limit)
    spent = max(corrected_spent, sum(c.spent for c in campaigns))
    minimum = minimum_budget(spent, period.reserve_bp)
    if new_hard_limit < minimum:
        return BudgetChangePlan(new, (), True, minimum)
    headroom = {c.campaign_id: c.limit - c.spent for c in campaigns if c.limit > c.spent}
    total = sum(headroom.values())
    allowed = min(
        new.operating_budget - sum(c.spent for c in campaigns),
        new_hard_limit - corrected_spent,
    )
    if total <= allowed:
        return BudgetChangePlan(new, (), False, minimum)
    cuts = tuple(
        LimitChange(c.campaign_id, c.spent + headroom[c.campaign_id] * allowed // total)
        for c in campaigns
        if c.campaign_id in headroom
        and c.spent + headroom[c.campaign_id] * allowed // total < c.limit
    )
    return BudgetChangePlan(new, cuts, False, minimum)
