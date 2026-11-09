from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from adhelper.budget.application.guard import check_change
from adhelper.budget.domain.limits import BudgetPeriod, LimitChange
from adhelper.budget.domain.owner import check_resume
from adhelper.budget.domain.ports import BudgetPlatform, PeriodStore, PlatformUnavailable
from adhelper.budget.domain.rules import BudgetRules
from adhelper.budget.domain.watch import BurnRate, burn_from_history, corrected_spend, freshest
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
    existing_campaign_id: Callable[[str, str], Awaitable[str | None]] | None = None

    async def __call__(self, change: Change) -> str | None:
        if change.kind is ChangeKind.RESUME:
            return await self._resume(change)
        if change.kind is ChangeKind.CREATE_CAMPAIGN:
            return await self._create(change)
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

    async def _create(self, change: Change) -> str | None:
        """Новая кампания: её лимит вместе со всеми лимитами кабинета в рамках рабочего бюджета.

        Считаем и приостановленные кампании, и созданные вне сервиса: их могут включить.
        """
        record = await self.store.active_for(self.business_id, change.account, self.clock())
        if record is None:
            return "no_active_period"
        if record.stop_pending:
            return "budget_stop_pending"
        ends_at = datetime.fromisoformat(change.planned["ends_at"])
        if record.period.ends_at is not None and ends_at > record.period.ends_at:
            return "ends_after_period"
        try:
            campaigns = await self.platform.read_account_campaigns(change.account)
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error
        aggregate_limit = await self.store.account_hard_limit(change.account, self.clock())
        aggregate_period = BudgetPeriod(
            aggregate_limit or record.period.hard_limit,
            record.period.reserve_bp,
            record.period.starts_at,
            record.period.ends_at,
        )
        existing_id = (
            await self.existing_campaign_id(change.account, change.target_ref)
            if self.existing_campaign_id is not None
            else None
        )
        existing = next((c for c in campaigns if c.campaign_id == existing_id), None)
        # A retry may find a campaign the previous attempt created before failing. Count its
        # current limit once, using the larger of current and planned limits for a safe ceiling.
        total = sum(c.limit for c in campaigns if c is not existing)
        total += max(existing.limit if existing is not None else 0, int(change.planned["limit"]))
        if total > aggregate_period.operating_budget:
            return "exceeds_operating_budget"
        return None

    async def _resume(self, change: Change) -> str | None:
        """Включение кампании проверяем по свежему факту: пока она стояла, деньги могли уйти."""
        now = self.clock()
        record = await self.store.active_for(self.business_id, change.account, now)
        if record is None:
            return "no_active_period"
        if record.stop_pending:
            return "budget_stop_pending"
        history = await self.store.recent_snapshots(record.id)
        burn = burn_from_history(history)
        try:
            campaigns = await self.platform.read_account_campaigns(change.account)
            snapshot = await self.platform.fetch_account_spend(change.account)
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error
        spent = corrected_spend(freshest(snapshot, history), burn, now)
        period_spent = max(spent - (record.opening_spent or 0), 0)
        aggregate_limit = await self.store.account_hard_limit(change.account, now)
        aggregate_period = BudgetPeriod(
            aggregate_limit or record.period.hard_limit,
            record.period.reserve_bp,
            record.period.starts_at,
            record.period.ends_at,
        )
        reason = check_resume(aggregate_period, campaigns, change.target_ref, period_spent, now)
        return reason.value if reason else None
