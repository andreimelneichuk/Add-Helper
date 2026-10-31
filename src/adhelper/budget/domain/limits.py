from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from adhelper.budget.domain.rules import (
    BASIS_POINTS,
    BudgetRules,
    ceil_share,
    floor_share,
    require_int,
)


@dataclass(frozen=True)
class BudgetPeriod:
    hard_limit: int
    reserve_bp: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    def __post_init__(self) -> None:
        require_int(hard_limit=self.hard_limit, reserve_bp=self.reserve_bp)
        if self.hard_limit < 0 or not 0 <= self.reserve_bp <= BASIS_POINTS:
            raise ValueError("Недопустимый бюджет или резерв")

    @property
    def operating_budget(self) -> int:
        return floor_share(self.hard_limit, BASIS_POINTS - self.reserve_bp)


@dataclass(frozen=True)
class CampaignBudget:
    campaign_id: str
    limit: int
    spent: int
    active: bool
    last_increase_at: datetime | None

    def __post_init__(self) -> None:
        require_int(limit=self.limit, spent=self.spent)


@dataclass(frozen=True)
class LimitChange:
    campaign_id: str
    new_limit: int

    def __post_init__(self) -> None:
        require_int(new_limit=self.new_limit)


class RejectionReason(StrEnum):
    UNKNOWN_CAMPAIGN = "unknown_campaign"
    BELOW_SPENT = "below_spent"
    STEP_TOO_LARGE = "step_too_large"
    TOO_SOON = "too_soon"
    PERIOD_ENDED = "period_ended"
    EXCEEDS_OPERATING_BUDGET = "exceeds_operating_budget"
    WORST_CASE_EXCEEDS_HARD_LIMIT = "worst_case_exceeds_hard_limit"


@dataclass(frozen=True)
class LimitDecision:
    """Результат проверки вместе с входными данными и версией правил."""

    reason: RejectionReason | None
    rules_version: str
    spent: int
    worst_active: int
    worst_change: int
    limit_sum_after: int
    operating_budget: int
    hard_limit: int

    @property
    def allowed(self) -> bool:
        return self.reason is None


def check_limit_change(
    period: BudgetPeriod,
    campaigns: Sequence[CampaignBudget],
    change: LimitChange,
    corrected_spent: int,
    now: datetime,
    rules: BudgetRules,
) -> LimitDecision:
    """Проверка худшего случая. Снижение уменьшает риск, поэтому ей не блокируется."""
    target = next((c for c in campaigns if c.campaign_id == change.campaign_id), None)
    limit_sum_after = (
        sum(c.limit for c in campaigns) + change.new_limit - (target.limit if target else 0)
    )
    worst_active = sum(max(c.limit - c.spent, 0) for c in campaigns if c.active)
    worst_change = 0
    reason: RejectionReason | None
    if target is None:
        reason = RejectionReason.UNKNOWN_CAMPAIGN
    elif change.new_limit < target.spent:
        reason = RejectionReason.BELOW_SPENT
    elif change.new_limit <= target.limit:
        reason = None
    else:
        worst_change = max(
            change.new_limit - target.limit,
            ceil_share(target.limit, rules.change_spike_old_bp)
            + ceil_share(change.new_limit, rules.change_spike_new_bp),
        )
        reason = _increase_rejection(
            period,
            target,
            change,
            corrected_spent,
            worst_active,
            worst_change,
            limit_sum_after,
            now,
            rules,
        )
    return LimitDecision(
        reason=reason,
        rules_version=rules.version,
        spent=corrected_spent,
        worst_active=worst_active,
        worst_change=worst_change,
        limit_sum_after=limit_sum_after,
        operating_budget=period.operating_budget,
        hard_limit=period.hard_limit,
    )


def _increase_rejection(
    period: BudgetPeriod,
    target: CampaignBudget,
    change: LimitChange,
    corrected_spent: int,
    worst_active: int,
    worst_change: int,
    limit_sum_after: int,
    now: datetime,
    rules: BudgetRules,
) -> RejectionReason | None:
    if period.ends_at is not None and now >= period.ends_at:
        return RejectionReason.PERIOD_ENDED
    if (change.new_limit - target.limit) * BASIS_POINTS > target.limit * rules.max_increase_bp:
        return RejectionReason.STEP_TOO_LARGE
    interval = timedelta(days=rules.min_increase_interval_days)
    if target.last_increase_at is not None and now - target.last_increase_at < interval:
        return RejectionReason.TOO_SOON
    if limit_sum_after > period.operating_budget:
        return RejectionReason.EXCEEDS_OPERATING_BUDGET
    if corrected_spent + worst_active + worst_change > period.hard_limit:
        return RejectionReason.WORST_CASE_EXCEEDS_HARD_LIMIT
    return None
