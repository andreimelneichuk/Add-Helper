from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from adhelper.budget.application.guard import check_change
from adhelper.budget.domain.limits import BudgetPeriod, LimitChange
from adhelper.budget.domain.ports import BudgetPlatform, PeriodStore, PlatformUnavailable
from adhelper.budget.domain.rules import BudgetRules
from adhelper.budget.domain.watch import BurnRate, burn_from_history
from adhelper.ledger.domain.model import Change, ChangeKind
from adhelper.ledger.domain.ports import GatewayUnavailable


@dataclass(frozen=True)
class BudgetPreflight:
    """Повторная проверка бюджета перед применением.

    Между записью в журнал и применением мог пройти сбой, поэтому прежнее разрешение устарело.
    """

    platform: BudgetPlatform
    period: BudgetPeriod
    burn: BurnRate
    clock: Callable[[], datetime]
    rules: BudgetRules

    async def __call__(self, change: Change) -> str | None:
        if change.kind is not ChangeKind.SET_LIMIT:
            return None
        try:
            decision = await check_change(
                self.platform,
                change.account,
                self.period,
                LimitChange(change.target_ref, change.planned["limit"]),
                self.burn,
                self.clock(),
                self.rules,
            )
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error
        return decision.reason.value if decision.reason else None


@dataclass(frozen=True)
class StoredPeriodPreflight:
    """Находит действующий период; без него рост проверить не с чем, поэтому он запрещён."""

    store: PeriodStore
    platform: BudgetPlatform
    business_id: UUID
    clock: Callable[[], datetime]
    rules_for: Callable[[str], BudgetRules]

    async def __call__(self, change: Change) -> str | None:
        if change.kind is not ChangeKind.SET_LIMIT:
            return None
        now = self.clock()
        record = await self.store.active_for(self.business_id, change.account, now)
        if record is None:
            increase = change.planned["limit"] > change.before.get("limit", 0)
            return "no_active_period" if increase else None
        burn = burn_from_history(await self.store.recent_snapshots(record.id))
        rules = self.rules_for(record.rules_version)
        return await BudgetPreflight(self.platform, record.period, burn, self.clock, rules)(change)
