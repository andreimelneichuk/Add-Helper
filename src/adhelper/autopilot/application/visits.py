"""Визиты Метрики для проверки качества данных

Отчёты Директа дают клики, Метрика — визиты с рекламы. Если визитов намного меньше кликов,
счётчик сломан, и выводы об эффективности делать нельзя. Визиты неизвестны (None), когда
счётчика нет, у Метрики нет данных за окно или она не ответила: это «данных нет», а не ноль.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Protocol

from adhelper.autopilot.application.cycle import AutopilotData
from adhelper.autopilot.domain.model import AccountData
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.budget.domain.calendar import platform_date
from adhelper.budget.domain.ports import PlatformUnavailable

logger = logging.getLogger(__name__)

CounterSource = Callable[[], Awaitable[str | None]]


class SiteVisits(Protocol):
    """Визиты с рекламы по счётчику за московские дни, обе границы входят.

    None — у Метрики нет данных за эти дни. Сбой связи — `PlatformUnavailable`.
    """

    async def ad_visits(self, counter: str, since: date, until: date) -> int | None: ...


class WithVisits:
    """Данные цикла, дополненные визитами за то же окно, что и клики."""

    def __init__(
        self,
        inner: AutopilotData,
        visits: SiteVisits,
        counter: CounterSource,
        rules: AutopilotRules,
    ) -> None:
        self._inner = inner
        self._visits = visits
        self._counter = counter
        self._rules = rules

    async def load(self, account: str, now: datetime) -> AccountData:
        data = await self._inner.load(account, now)
        counter = await self._counter()
        if counter is None:
            return data
        until = platform_date(now)
        since = until - timedelta(days=self._rules.health_recent_days - 1)
        try:
            visits = await self._visits.ad_visits(counter, since, until)
        except PlatformUnavailable:
            logger.warning("Метрика не ответила: визиты за окно неизвестны")
            visits = None
        return replace(data, recent=replace(data.recent, visits=visits))
