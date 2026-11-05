from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from adhelper.budget.domain.balance import BalanceBaseline
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, LimitChange, LimitDecision
from adhelper.budget.domain.watch import SpendSnapshot


class PlatformUnavailable(Exception):
    """Площадка не ответила; применилось ли действие, неизвестно."""


class BudgetPlatform(Protocol):
    async def read_campaigns(self, account: str) -> Sequence[CampaignBudget]: ...
    async def fetch_spend(self, account: str) -> SpendSnapshot: ...
    async def fetch_balance(self, account: str) -> int: ...
    async def set_limit(self, account: str, change: LimitChange) -> None: ...
    async def pause_campaigns(self, account: str, campaign_ids: list[str]) -> None: ...
    async def resume_campaigns(self, account: str, campaign_ids: list[str]) -> None: ...
    async def record_rejection(
        self, account: str, change: LimitChange, decision: LimitDecision
    ) -> None: ...


@dataclass(frozen=True)
class PeriodRecord:
    id: int
    business_id: UUID
    account: str
    period: BudgetPeriod
    rules_version: str
    opening_balance: int | None
    stop_pending: bool
    # Расход по статистике в момент, когда снят opening_balance.
    opening_spent: int | None = None
    last_balance: int | None = None
    balance_checked_at: datetime | None = None


class PeriodStore(Protocol):
    async def due(self, now: datetime) -> Sequence[PeriodRecord]: ...
    async def active_for(
        self, business_id: UUID, account: str, now: datetime
    ) -> PeriodRecord | None: ...
    async def recent_snapshots(self, period_id: int) -> Sequence[SpendSnapshot]: ...
    async def save_cycle(
        self,
        period_id: int,
        snapshot: SpendSnapshot | None,
        stop_pending: bool,
        next_check_at: datetime,
    ) -> None: ...
    async def reschedule(self, period_id: int, next_check_at: datetime) -> None: ...
    async def save_balance(
        self,
        period_id: int,
        balance: int,
        checked_at: datetime,
        baseline: BalanceBaseline | None,
    ) -> None: ...
    async def latest_spent(self, period_id: int) -> int | None: ...
    async def set_hard_limit(self, period_id: int, hard_limit: int) -> None: ...


class AlertSink(Protocol):
    async def raise_alert(self, kind: str, details: Mapping[str, Any]) -> None: ...
    async def resolve(self, kind: str) -> None: ...
