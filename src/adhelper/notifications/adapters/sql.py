from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Index, Text, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.db import Base

OPEN_ALERT = text("resolved_at IS NULL")


class AlertRow(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_open_business_id_kind",
            "business_id",
            "kind",
            unique=True,
            postgresql_where=OPEN_ALERT,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SqlAlerts:
    """Открытые проблемы бизнеса. Одна проблема одного вида открыта не более одного раза."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    @staticmethod
    def factory(sessions: async_sessionmaker[AsyncSession]) -> Callable[[UUID], "SqlAlerts"]:
        return lambda business_id: SqlAlerts(sessions, business_id)

    async def raise_alert(self, kind: str, details: Mapping[str, Any]) -> None:
        statement = (
            insert(AlertRow)
            .values(business_id=self._business_id, kind=kind, details=dict(details))
            .on_conflict_do_nothing(index_elements=["business_id", "kind"], index_where=OPEN_ALERT)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def resolve(self, kind: str) -> None:
        statement = (
            update(AlertRow)
            .where(
                AlertRow.business_id == self._business_id,
                AlertRow.kind == kind,
                AlertRow.resolved_at.is_(None),
            )
            .values(resolved_at=func.now())
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def open_kinds(self) -> list[str]:
        statement = (
            select(AlertRow.kind)
            .where(AlertRow.business_id == self._business_id, AlertRow.resolved_at.is_(None))
            .order_by(AlertRow.id)
        )
        async with self._sessions() as session:
            return list(await session.scalars(statement))
