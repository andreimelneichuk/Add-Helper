"""Тест по итогам анализа: гипотеза из профиля и подготовка теста. docs/04-autopilot.md §5."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.experiments.adapters.sql import SqlExperimentStore
from adhelper.experiments.application.service import (
    Hypothesis,
    TestAlreadyRunning,
    pick_hypothesis,
    supported_hypotheses,
)

PREPARED = "prepared"
BUSY = "test_running"


class SqlTestPlanner:
    __test__ = False

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], business_id: UUID, rules_version: str
    ) -> None:
        self._profiles = SqlProfiles(sessions, business_id)
        self._store = SqlExperimentStore(sessions, business_id)
        self._rules_version = rules_version

    async def hypothesis(self, account: str, ad_ref: str) -> str | None:
        found = await self._profiles.get()
        if found is None:
            return None
        profile = found[0]
        supported = supported_hypotheses(
            has_prices=any(service.price for service in profile.services),
            has_features=bool(profile.features),
        )
        chosen = pick_hypothesis(supported, await self._store.tried(account, ad_ref))
        return chosen.value if chosen else None

    async def prepare(
        self, account: str, campaign_id: str, ad_ref: str, hypothesis: str, decision_id: int
    ) -> str:
        try:
            await self._store.add(
                account,
                campaign_id,
                ad_ref,
                None,
                Hypothesis(hypothesis).value,
                self._rules_version,
            )
        except TestAlreadyRunning:
            return BUSY
        return PREPARED
