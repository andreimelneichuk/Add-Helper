"""Лимиты, темп расхода и стратегия кампаний с площадки в данных автопилота.

Витрина лимитов не хранит, поэтому их читаем с площадки при каждом разборе: тем же чтением,
что и страж бюджета. Площадка не ответила — оставляем осторожные значения витрины (лимит 0,
«не упирается»): рост не планируется, а тест считается «бюджет мал». docs/04-autopilot.md §4.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import date, datetime, time
from typing import Protocol

from adhelper.autopilot.application.cycle import AutopilotData
from adhelper.autopilot.domain.budgets import budget_limited
from adhelper.autopilot.domain.model import AccountData, CampaignStats
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.budget.domain.calendar import MOSCOW
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget
from adhelper.budget.domain.ports import CampaignStrategy, PlatformUnavailable

logger = logging.getLogger(__name__)

__all__ = ["CampaignStrategy", "WithBudgets"]


class CampaignBudgets(Protocol):
    async def read_campaigns(self, account: str) -> list[CampaignBudget]: ...


PeriodSource = Callable[[], Awaitable[BudgetPeriod | None]]
StrategySource = Callable[[str], Awaitable[Mapping[str, CampaignStrategy]]]


def _started_at(day: date | None) -> datetime | None:
    return datetime.combine(day, time.min, tzinfo=MOSCOW) if day else None


class WithBudgets:
    def __init__(
        self,
        inner: AutopilotData,
        platform: CampaignBudgets,
        period: PeriodSource,
        rules: AutopilotRules,
        strategies: StrategySource | None = None,
    ) -> None:
        self._inner = inner
        self._platform = platform
        self._period = period
        self._rules = rules
        self._strategies = strategies

    async def load(self, account: str, now: datetime) -> AccountData:
        data = await self._inner.load(account, now)
        try:
            budgets = {c.campaign_id: c for c in await self._platform.read_campaigns(account)}
            strategies = await self._strategies(account) if self._strategies else {}
        except PlatformUnavailable:
            logger.warning("Площадка не ответила: лимиты кампаний неизвестны, рост не планируем")
            return data
        period = await self._period()
        campaigns = [
            self._merge(c, budgets.get(c.campaign_id), strategies.get(c.campaign_id), period, now)
            for c in data.campaigns
        ]
        return replace(data, campaigns=campaigns)

    def _merge(
        self,
        stats: CampaignStats,
        budget: CampaignBudget | None,
        strategy: CampaignStrategy | None,
        period: BudgetPeriod | None,
        now: datetime,
    ) -> CampaignStats:
        if budget is None:
            return stats
        limited = budget_limited(
            budget.limit,
            budget.spent,
            period.starts_at if period else None,
            period.ends_at if period else None,
            now,
            self._rules.budget_limited_pace_bp,
        )
        known = [
            moment
            for moment in (stats.last_change_at, budget.last_increase_at)
            if moment is not None
        ]
        started = _started_at(strategy.started) if strategy else None
        # Изменений в журнале нет — лимит не менялся с запуска кампании.
        last_change = max(known) if known else started
        return replace(
            stats,
            limit=budget.limit,
            budget_limited=limited,
            last_change_at=last_change,
            auto_strategy=strategy.auto if strategy else stats.auto_strategy,
            in_learning=strategy.learns if strategy else stats.in_learning,
        )
