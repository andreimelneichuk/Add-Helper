from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.budget.adapters.rules_file import load_rules
from adhelper.notifications.domain.model import MoneyFacts
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.overview import overview


class DashboardMoney:
    """Цифры для тревоги — те же, что владелец видит на главной кабинета."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def money(self, business_id: Any, now: datetime) -> MoneyFacts | None:
        view = await overview(
            SqlDashboard(self._sessions, business_id),
            now,
            lambda version: load_rules(version).balance_tolerance_bp,
            days=1,
        )
        if not view.money:
            return None
        money = view.money[0]
        return MoneyFacts(
            hard_limit=money.period.hard_limit,
            spendable=money.spendable,
            tolerance=money.balance_tolerance,
            spent=money.period.spent,
            last_balance=money.period.last_balance,
            balance_gap=money.balance_gap,
        )
