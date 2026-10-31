from collections.abc import Sequence
from typing import Protocol

from adhelper.budget.domain.limits import CampaignBudget, LimitChange, LimitDecision
from adhelper.budget.domain.watch import SpendSnapshot


class PlatformUnavailable(Exception):
    """Площадка не ответила; применилось ли действие, неизвестно."""


class BudgetPlatform(Protocol):
    async def read_campaigns(self, account: str) -> Sequence[CampaignBudget]: ...
    async def fetch_spend(self, account: str) -> SpendSnapshot: ...
    async def set_limit(self, account: str, change: LimitChange) -> None: ...
    async def pause_campaigns(self, account: str, campaign_ids: list[str]) -> None: ...
    async def record_rejection(
        self, account: str, change: LimitChange, decision: LimitDecision
    ) -> None: ...
