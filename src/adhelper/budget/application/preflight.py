from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from adhelper.budget.application.guard import check_change
from adhelper.budget.domain.limits import BudgetPeriod, LimitChange
from adhelper.budget.domain.ports import BudgetPlatform, PlatformUnavailable
from adhelper.budget.domain.rules import BudgetRules
from adhelper.budget.domain.watch import BurnRate
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
