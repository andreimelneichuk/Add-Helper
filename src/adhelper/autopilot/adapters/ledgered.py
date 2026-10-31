from collections.abc import Awaitable, Callable

from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from adhelper.ledger.domain.ports import GatewayUnavailable


class LedgeredActions:
    """Действия автопилота: только через журнал и, для денег, через страж бюджета."""

    def __init__(
        self,
        ledger: Ledger,
        increase: Callable[[str, str, int], Awaitable[str]],
    ) -> None:
        self._ledger = ledger
        self._increase = increase

    async def pause_ad(self, account: str, ad_ref: str, explanation: str, decision_id: int) -> str:
        proposed = ProposedChange(
            account=account,
            source=ChangeSource.AUTOPILOT,
            kind=ChangeKind.PAUSE_AD,
            target_ref=ad_ref,
            planned={"active": False},
            explanation=explanation,
            idempotency_key=f"pause_ad:{ad_ref}",
            decision_id=str(decision_id),
        )
        try:
            change = await self._ledger.submit(proposed)
            await self._ledger.execute_pending(account)
        except GatewayUnavailable:
            # Строка журнала, если успела записаться, завершится согласованием state.reconcile.
            return "pending"
        current = await self._ledger.status_of(change.id)
        return "executed" if current is ChangeStatus.APPLIED else "pending"

    async def increase_limit(self, account: str, campaign_id: str, new_limit: int) -> str:
        return await self._increase(account, campaign_id, new_limit)
