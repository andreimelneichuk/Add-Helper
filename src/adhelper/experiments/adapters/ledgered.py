from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from adhelper.ledger.domain.ports import GatewayUnavailable


def _kind(active: bool) -> ChangeKind:
    return ChangeKind.RESUME_AD if active else ChangeKind.PAUSE_AD


class LedgeredSwitch:
    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    async def set_active(
        self, account: str, ad_ref: str, active: bool, explanation: str, key: str
    ) -> bool:
        proposed = ProposedChange(
            account=account,
            source=ChangeSource.EXPERIMENT,
            kind=_kind(active),
            target_ref=ad_ref,
            planned={"active": active},
            explanation=explanation,
            idempotency_key=key,
        )
        try:
            change = await self._ledger.submit(proposed)
            await self._ledger.execute_pending(account)
        except GatewayUnavailable:
            return False
        return await self._ledger.status_of(change.id) is ChangeStatus.APPLIED

    async def record_external(
        self, account: str, ad_ref: str, expected: bool, actual: bool, explanation: str, key: str
    ) -> None:
        await self._ledger.record_external(
            ProposedChange(
                account=account,
                source=ChangeSource.EXTERNAL,
                kind=_kind(actual),
                target_ref=ad_ref,
                planned={"active": actual},
                explanation=explanation,
                idempotency_key=key,
            ),
            {"active": expected},
        )
