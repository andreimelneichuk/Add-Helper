from collections.abc import Mapping, Sequence
from typing import Any

from adhelper.budget.domain.limits import (
    CampaignBudget,
    LimitChange,
    LimitDecision,
)
from adhelper.budget.domain.ports import BudgetPlatform, PlatformUnavailable
from adhelper.budget.domain.watch import SpendSnapshot
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ProposedChange
from adhelper.ledger.domain.ports import GatewayUnavailable


def guard_source() -> ChangeSource:
    return ChangeSource.BUDGET_GUARD


def limit_proposal(
    account: str, source: ChangeSource, campaign_id: str, new_limit: int
) -> ProposedChange:
    return ProposedChange(
        account=account,
        source=source,
        kind=ChangeKind.SET_LIMIT,
        target_ref=campaign_id,
        planned={"limit": new_limit},
        explanation=f"Лимит кампании {campaign_id}: {new_limit}",
        idempotency_key=f"set_limit:{campaign_id}:{new_limit}",
    )


class PlatformChangeGateway:
    """Исполнитель журнала поверх площадки: единственное место, где она реально меняется."""

    def __init__(self, platform: BudgetPlatform) -> None:
        self._platform = platform

    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]:
        try:
            campaigns = await self._platform.read_campaigns(account)
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error
        for campaign in campaigns:
            if campaign.campaign_id == target_ref:
                return {"limit": campaign.limit, "active": campaign.active}
        return {}

    async def apply(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        try:
            if kind is ChangeKind.SET_LIMIT:
                await self._platform.set_limit(account, LimitChange(target_ref, planned["limit"]))
            else:
                await self._platform.pause_campaigns(account, [target_ref])
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error


class LedgeredBudgetPlatform:
    """Площадка для стража бюджета: чтение напрямую, изменения только через журнал."""

    def __init__(self, platform: BudgetPlatform, ledger: Ledger, source: ChangeSource) -> None:
        self._platform = platform
        self._ledger = ledger
        self._source = source

    async def read_campaigns(self, account: str) -> Sequence[CampaignBudget]:
        return await self._platform.read_campaigns(account)

    async def fetch_spend(self, account: str) -> SpendSnapshot:
        return await self._platform.fetch_spend(account)

    async def fetch_balance(self, account: str) -> int:
        return await self._platform.fetch_balance(account)

    def _proposal(self, account: str, change: LimitChange) -> ProposedChange:
        return limit_proposal(account, self._source, change.campaign_id, change.new_limit)

    async def _run(self, proposed: ProposedChange) -> None:
        try:
            await self._ledger.submit(proposed)
            await self._ledger.execute_pending(proposed.account)
        except GatewayUnavailable as error:
            raise PlatformUnavailable(str(error)) from error

    async def set_limit(self, account: str, change: LimitChange) -> None:
        await self._run(self._proposal(account, change))

    async def pause_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        for campaign_id in campaign_ids:
            await self._run(
                ProposedChange(
                    account=account,
                    source=self._source,
                    kind=ChangeKind.PAUSE,
                    target_ref=campaign_id,
                    planned={"active": False},
                    explanation=f"Кампания {campaign_id} приостановлена ради бюджета",
                    idempotency_key=f"pause:{campaign_id}",
                )
            )

    async def record_rejection(
        self, account: str, change: LimitChange, decision: LimitDecision
    ) -> None:
        reason = decision.reason.value if decision.reason else "rejected"
        await self._ledger.record_rejection(self._proposal(account, change), reason)
