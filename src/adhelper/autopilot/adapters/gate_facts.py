"""Факты для ворот политики из журнала изменений и действующего бюджетного периода.

Запросы ограничены business_id. Что неизвестно, отдаём как неизвестное: рабочий бюджет без
периода равен нулю (рост лимита не пройдёт), число оставшихся баллов API не отслеживается и
остаётся None, что политика не принимает за запас.
"""

from datetime import datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.application.gate import GateFacts
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.domain.calendar import MOSCOW, platform_date
from adhelper.ledger.adapters.sql import ChangeRow
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus

WEEK = timedelta(days=7)


class SqlGateFacts:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        business_id: UUID,
        periods: SqlPeriodStore,
    ) -> None:
        self._sessions = sessions
        self._business_id = business_id
        self._periods = periods

    async def load(self, account: str, now: datetime) -> GateFacts:
        record = await self._periods.active_for(self._business_id, account, now)
        midnight = datetime.combine(platform_date(now), time.min, tzinfo=MOSCOW)
        statement = select(ChangeRow).where(
            ChangeRow.business_id == self._business_id,
            ChangeRow.account == account,
            ChangeRow.status == ChangeStatus.APPLIED.value,
            ChangeRow.reverts_change_id.is_(None),
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
        changes_today = moved = 0
        last: dict[str, datetime] = {}
        for row in rows:
            at = row.updated_at or row.created_at
            # Рост и снижение (в том числе донором) одинаково считаются изменением лимита.
            is_limit = row.kind == ChangeKind.SET_LIMIT.value
            if is_limit and (row.target_ref not in last or at > last[row.target_ref]):
                last[row.target_ref] = at
            if row.source != ChangeSource.AUTOPILOT.value:
                continue
            if at >= midnight:
                changes_today += 1
            if is_limit and at >= now - WEEK:
                moved += max(int(row.planned.get("limit", 0)) - int(row.before.get("limit", 0)), 0)
        return GateFacts(
            changes_today=changes_today,
            moved_this_week=moved,
            data_alert=False,
            api_points_rest=None,
            operating_budget=record.period.operating_budget if record else 0,
            last_limit_change=last,
        )
