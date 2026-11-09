"""Хранение экономики бизнеса. Каждый запрос ограничен business_id."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, Uuid, column, func, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.business.domain.economics import Economics

ECONOMICS = table(
    "business_economics",
    column("business_id", Uuid),
    column("avg_check_micros", BigInteger),
    column("lead_to_client_bp", Integer),
    column("margin_bp", Integer),
    column("repeat_bp", Integer),
    column("ad_share_bp", Integer),
    column("capacity_per_week", Integer),
    column("confirmed_at", DateTime(timezone=True)),
    column("updated_at", DateTime(timezone=True)),
)


@dataclass(frozen=True)
class StoredEconomics:
    economics: Economics
    avg_check_micros: int | None
    confirmed_at: datetime | None


class SqlEconomics:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def get(self) -> StoredEconomics | None:
        statement = select(ECONOMICS).where(ECONOMICS.c.business_id == self._business_id)
        async with self._sessions() as session:
            row = (await session.execute(statement)).one_or_none()
        if row is None:
            return None
        return StoredEconomics(
            Economics(
                lead_to_client_bp=row.lead_to_client_bp,
                margin_bp=row.margin_bp,
                repeat_bp=row.repeat_bp,
                ad_share_bp=row.ad_share_bp,
                capacity_per_week=row.capacity_per_week,
                confirmed=row.confirmed_at is not None,
            ),
            row.avg_check_micros,
            row.confirmed_at,
        )

    async def save(
        self, economics: Economics, avg_check_micros: int | None, confirmed_at: datetime | None
    ) -> None:
        values = {
            "avg_check_micros": avg_check_micros,
            "lead_to_client_bp": economics.lead_to_client_bp,
            "margin_bp": economics.margin_bp,
            "repeat_bp": economics.repeat_bp,
            "ad_share_bp": economics.ad_share_bp,
            "capacity_per_week": economics.capacity_per_week,
            "confirmed_at": confirmed_at,
        }
        statement = insert(ECONOMICS).values(business_id=self._business_id, **values)
        statement = statement.on_conflict_do_update(
            index_elements=[ECONOMICS.c.business_id],
            set_={**values, "updated_at": func.now()},
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)
