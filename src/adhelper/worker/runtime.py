from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import (
    LedgeredBudgetPlatform,
    PlatformChangeGateway,
    guard_source,
    limit_proposal,
)
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.preflight import StoredPeriodPreflight
from adhelper.budget.application.watch_cycle import run_watch_cycle
from adhelper.config import Settings
from adhelper.db import create_engine
from adhelper.ledger.adapters.sql import (
    PostgresAccountLock,
    SqlChangeRepository,
    accounts_with_open_changes,
)
from adhelper.ledger.application.service import Ledger, reconcile_open_changes
from adhelper.notifications.adapters.sql import SqlAlerts

MAX_CHANGE_ATTEMPTS = 3


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Runtime:
    """Точка сборки фоновых задач."""

    sessions: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    # Адаптер настоящей площадки появится в T5; пока площадки нет, бизнес получает тревогу.
    platform_for: Callable[[UUID, str], Any]
    clock: Callable[[], datetime] = _utc_now

    def ledger_for(self, business_id: UUID, account: str) -> Ledger:
        platform = self.platform_for(business_id, account)
        if platform is None:
            raise LookupError("Площадка не подключена")
        return Ledger(
            repository=SqlChangeRepository(self.sessions, business_id),
            gateway=PlatformChangeGateway(platform),
            lock=PostgresAccountLock(self.engine),
            max_attempts=MAX_CHANGE_ATTEMPTS,
            preflight=StoredPeriodPreflight(
                SqlPeriodStore(self.sessions), platform, business_id, self.clock, load_rules
            ),
        )

    def limit_proposal(self, account: str, campaign_id: str, new_limit: int) -> Any:
        return limit_proposal(account, guard_source(), campaign_id, new_limit)

    def _guarded_platform(self, business_id: UUID, account: str) -> LedgeredBudgetPlatform | None:
        platform = self.platform_for(business_id, account)
        if platform is None:
            return None
        return LedgeredBudgetPlatform(
            platform, self.ledger_for(business_id, account), guard_source()
        )

    async def budget_watch(self) -> None:
        await run_watch_cycle(
            SqlPeriodStore(self.sessions),
            self._guarded_platform,
            SqlAlerts.factory(self.sessions),
            self.clock(),
            load_rules,
        )

    async def state_reconcile(self) -> None:
        def ledger_or_none(business_id: UUID, account: str) -> Ledger | None:
            if self.platform_for(business_id, account) is None:
                return None
            return self.ledger_for(business_id, account)

        await reconcile_open_changes(
            await accounts_with_open_changes(self.sessions), ledger_or_none
        )


def _no_platform(business_id: UUID, account: str) -> None:
    return None


@asynccontextmanager
async def open_runtime(settings: Settings) -> AsyncIterator[Runtime]:
    engine = create_engine(settings)
    try:
        yield Runtime(
            sessions=async_sessionmaker(engine, expire_on_commit=False),
            engine=engine,
            platform_for=_no_platform,
        )
    finally:
        await engine.dispose()
