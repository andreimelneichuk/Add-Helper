"""Факты для ворот политики из журнала изменений и действующего бюджетного периода.

Запросы ограничены business_id. Что неизвестно, отдаём как неизвестное: рабочий бюджет без
периода равен нулю (рост лимита не пройдёт), число оставшихся баллов API не отслеживается и
остаётся None, что политика не принимает за запас.
"""

from datetime import datetime, time, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.domain.policy import GateFacts
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.domain.calendar import MOSCOW, platform_date
from adhelper.ledger.adapters.sql import ChangeRow
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus

WEEK = timedelta(days=7)
TRANSFER_PREFIX = "transfer:"
# Незавершённое тоже занимает место в счётчиках: оно уже решено и вот-вот выполнится.
COUNTED = (
    ChangeStatus.APPLIED.value,
    ChangeStatus.PLANNED.value,
    ChangeStatus.APPLYING.value,
)


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
        period = record.period if record else None
        # Период, который уже закончился, не граница: рабочий бюджет неизвестен, рост не пройдёт.
        current = period is not None and period.ends_at is not None and period.ends_at > now
        midnight = datetime.combine(platform_date(now), time.min, tzinfo=MOSCOW)
        since = min(midnight, now - WEEK)
        counted = select(ChangeRow).where(
            ChangeRow.business_id == self._business_id,
            ChangeRow.account == account,
            ChangeRow.status.in_(COUNTED),
            ChangeRow.updated_at >= since,
        )
        latest = (
            select(ChangeRow.target_ref, func.max(ChangeRow.updated_at))
            .where(
                ChangeRow.business_id == self._business_id,
                ChangeRow.account == account,
                ChangeRow.status.in_(COUNTED),
                ChangeRow.kind == ChangeKind.SET_LIMIT.value,
            )
            .group_by(ChangeRow.target_ref)
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(counted))
            last = {ref: at for ref, at in await session.execute(latest)}
        changes_today = moved = 0
        for row in rows:
            # Перенос идёт под источником стража бюджета, но это решение автопилота: считаем его.
            if row.source != ChangeSource.AUTOPILOT.value and not (
                row.decision_id or ""
            ).startswith(TRANSFER_PREFIX):
                continue
            at = row.updated_at
            if at >= midnight:
                changes_today += 1
            if row.kind == ChangeKind.SET_LIMIT.value and at >= now - WEEK:
                moved += max(int(row.planned.get("limit", 0)) - int(row.before.get("limit", 0)), 0)
        return GateFacts(
            changes_today=changes_today,
            moved_this_week=moved,
            data_alert=False,
            api_points_rest=None,
            operating_budget=period.operating_budget if period is not None and current else 0,
            last_limit_change=last,
        )
