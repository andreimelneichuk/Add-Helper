from collections.abc import Mapping
from dataclasses import dataclass

from adhelper.budget.domain.limits import CampaignBudget, LimitChange


class TransferRejected(ValueError):
    pass


@dataclass(frozen=True)
class TransferPlan:
    donor_id: str
    recipient_id: str
    donor_target: int
    recipient_target: int
    # Лимит донора до переноса: к нему возвращаем, если рост получателя невозможен.
    donor_start: int | None = None


def plan_transfer(donor: CampaignBudget, recipient: CampaignBudget, amount: int) -> TransferPlan:
    if donor.campaign_id == recipient.campaign_id:
        raise TransferRejected("Перенос внутри одной кампании")
    if amount <= 0:
        raise TransferRejected("Сумма переноса должна быть положительной")
    if donor.limit - amount < donor.spent:
        raise TransferRejected("Лимит донора стал бы ниже его расхода")
    return TransferPlan(
        donor_id=donor.campaign_id,
        recipient_id=recipient.campaign_id,
        donor_target=donor.limit - amount,
        recipient_target=recipient.limit + amount,
        donor_start=donor.limit,
    )


def next_step(plan: TransferPlan, actual_limits: Mapping[str, int]) -> LimitChange | None:
    """Следующий шаг по фактическим лимитам: получатель растёт только после снижения донора."""
    if actual_limits[plan.donor_id] > plan.donor_target:
        return LimitChange(plan.donor_id, plan.donor_target)
    if actual_limits[plan.recipient_id] < plan.recipient_target:
        return LimitChange(plan.recipient_id, plan.recipient_target)
    return None
