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


def transfer_key_prefix(transfer_id: str) -> str:
    return f"transfer:{transfer_id}:"


def limit_proposal(
    account: str,
    source: ChangeSource,
    campaign_id: str,
    new_limit: int,
    transfer_id: str | None = None,
) -> ProposedChange:
    """С `transfer_id` ключ уникален для переноса: возврат не склеивается со старой записью."""
    key = f"set_limit:{campaign_id}:{new_limit}"
    if transfer_id is not None:
        key = transfer_key_prefix(transfer_id) + key
    return ProposedChange(
        account=account,
        source=source,
        kind=ChangeKind.SET_LIMIT,
        target_ref=campaign_id,
        planned={"limit": new_limit},
        explanation=f"Лимит кампании {campaign_id}: {new_limit}",
        idempotency_key=key,
        decision_id=f"transfer:{transfer_id}" if transfer_id is not None else None,
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
            elif kind is ChangeKind.RESUME:
                await self._platform.resume_campaigns(account, [target_ref])
            else:
                await self._platform.pause_campaigns(account, [target_ref])
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error


class LedgeredBudgetPlatform:
    """Площадка для стража бюджета: чтение напрямую, изменения только через журнал."""

    def __init__(
        self,
        platform: BudgetPlatform,
        ledger: Ledger,
        source: ChangeSource,
        transfer_id: str | None = None,
    ) -> None:
        self._platform = platform
        self._ledger = ledger
        self._source = source
        self._transfer_id = transfer_id

    def for_transfer(self, transfer_id: str) -> "LedgeredBudgetPlatform":
        """Та же площадка, но все лимиты пишутся под единым идентификатором переноса."""
        return LedgeredBudgetPlatform(self._platform, self._ledger, self._source, transfer_id)

    async def read_campaigns(self, account: str) -> Sequence[CampaignBudget]:
        campaigns = await self._platform.read_campaigns(account)
        increases = await self._ledger.last_limit_increases(account)
        if not increases:
            return campaigns
        return [
            CampaignBudget(
                campaign_id=c.campaign_id,
                limit=c.limit,
                spent=c.spent,
                active=c.active,
                last_increase_at=increases.get(c.campaign_id, c.last_increase_at),
            )
            for c in campaigns
        ]

    async def read_account_campaigns(self, account: str) -> Sequence[CampaignBudget]:
        return await self._platform.read_account_campaigns(account)

    async def fetch_spend(self, account: str) -> SpendSnapshot:
        return await self._platform.fetch_spend(account)

    async def fetch_account_spend(self, account: str) -> SpendSnapshot:
        return await self._platform.fetch_account_spend(account)

    async def fetch_balance(self, account: str) -> int:
        return await self._platform.fetch_balance(account)

    def _proposal(self, account: str, change: LimitChange) -> ProposedChange:
        return limit_proposal(
            account, self._source, change.campaign_id, change.new_limit, self._transfer_id
        )

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

    async def resume_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        for campaign_id in campaign_ids:
            await self._run(
                ProposedChange(
                    account=account,
                    source=self._source,
                    kind=ChangeKind.RESUME,
                    target_ref=campaign_id,
                    planned={"active": True},
                    explanation=f"Кампания {campaign_id} снова показывается",
                    idempotency_key=f"resume:{campaign_id}",
                )
            )

    async def archive_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        if hasattr(self._platform, "archive_campaigns"):
            await self._platform.archive_campaigns(account, campaign_ids)

    async def record_rejection(
        self, account: str, change: LimitChange, decision: LimitDecision
    ) -> None:
        reason = decision.reason.value if decision.reason else "rejected"
        await self._ledger.record_rejection(self._proposal(account, change), reason)
