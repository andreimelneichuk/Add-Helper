from dataclasses import dataclass, field

from adhelper.budget.domain.ports import BudgetPlatform, PlatformUnavailable
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ProposedChange
from adhelper.ledger.domain.ports import GatewayUnavailable

EXPLANATION = (
    "Реклама остановлена вручную оператором. Запустить снова можно только отдельным решением."
)


@dataclass
class StopReport:
    stopped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failed


async def stop_account(
    account: str, platform: BudgetPlatform, ledger: Ledger, report: StopReport
) -> None:
    """Сбой одной кампании не мешает остановить остальные; итог определяет чтение площадки."""
    try:
        campaigns = await platform.read_campaigns(account)
    except PlatformUnavailable:
        report.failed.append((account, "platform_unavailable"))
        return
    active = [campaign.campaign_id for campaign in campaigns if campaign.active]
    for campaign_id in active:
        proposed = ProposedChange(
            account=account,
            source=ChangeSource.USER,
            kind=ChangeKind.PAUSE,
            target_ref=campaign_id,
            planned={"active": False},
            explanation=EXPLANATION,
            idempotency_key=f"emergency:{campaign_id}",
        )
        try:
            await ledger.submit(proposed)
        except GatewayUnavailable:
            continue  # Записи нет, поэтому кампания попадёт в отчёт как не остановленная.
    if not active:
        return
    # Очередь строгая: чтобы дойти до следующей кампании, зависшая должна исчерпать попытки.
    after: dict[str, bool] = {}
    for _ in range(len(active) * (ledger.max_attempts + 1)):
        await ledger.execute_pending(account)
        try:
            campaigns = await platform.read_campaigns(account)
        except PlatformUnavailable:
            continue
        after = {item.campaign_id: item.active for item in campaigns}
        if not any(after.get(ref, True) for ref in active):
            break
    if not after:
        report.failed.extend((f"{account}:{ref}", "platform_unavailable") for ref in active)
        return
    for campaign_id in active:
        if after.get(campaign_id, True):
            report.failed.append((f"{account}:{campaign_id}", "still_active"))
        else:
            report.stopped.append(f"{account}:{campaign_id}")
