from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Index, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.db import Base

SHADOW_STATUS = "shadow"


class DecisionRow(Base):
    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_business_id_account", "business_id", "account"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID]
    account: Mapped[str] = mapped_column(Text)
    object_ref: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rules_version: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class LoggedDecision:
    id: int
    object_ref: str
    verdict: str
    action: str | None
    status: str
    inputs: Mapping[str, Any]
    rules_version: str
    decided_at: datetime
    account: str = ""


class SqlDecisionLog:
    """Решения только дополняются: смена статуса — новая запись, последняя действующая."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def record(
        self,
        account: str,
        object_ref: str,
        verdict: str,
        action: str | None,
        status: str,
        inputs: Mapping[str, Any],
        rules_version: str,
        decided_at: datetime,
    ) -> int:
        row = DecisionRow(
            business_id=self._business_id,
            account=account,
            object_ref=object_ref,
            verdict=verdict,
            action=action,
            status=status,
            inputs=dict(inputs),
            rules_version=rules_version,
            decided_at=decided_at,
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            return row.id

    @staticmethod
    def _logged(row: DecisionRow) -> LoggedDecision:
        return LoggedDecision(
            row.id,
            row.object_ref,
            row.verdict,
            row.action,
            row.status,
            row.inputs,
            row.rules_version,
            row.decided_at,
            row.account,
        )

    async def get(self, decision_id: int) -> LoggedDecision | None:
        statement = select(DecisionRow).where(
            DecisionRow.business_id == self._business_id, DecisionRow.id == decision_id
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).one_or_none()
        return self._logged(row) if row else None

    async def latest(self, account: str, object_ref: str) -> LoggedDecision | None:
        """Действующее решение по объекту: последняя запись."""
        statement = (
            select(DecisionRow)
            .where(
                DecisionRow.business_id == self._business_id,
                DecisionRow.account == account,
                DecisionRow.object_ref == object_ref,
            )
            .order_by(DecisionRow.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).one_or_none()
        return self._logged(row) if row else None

    async def shadow(self, account: str, limit: int = 200) -> Sequence[LoggedDecision]:
        """Теневые записи autopilot-3: что сделали бы правила, новые сверху. Только чтение."""
        statement = (
            select(DecisionRow)
            .where(
                DecisionRow.business_id == self._business_id,
                DecisionRow.account == account,
                DecisionRow.status == SHADOW_STATUS,
            )
            .order_by(DecisionRow.id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            return [self._logged(row) for row in await session.scalars(statement)]

    async def recent(self, account: str, limit: int = 100) -> Sequence[LoggedDecision]:
        """Последнее состояние каждого объекта, новые сверху."""
        statement = (
            select(DecisionRow)
            .where(
                DecisionRow.business_id == self._business_id,
                DecisionRow.account == account,
                DecisionRow.status != SHADOW_STATUS,
            )
            .order_by(DecisionRow.id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
        latest: dict[str, DecisionRow] = {}
        first: dict[str, int] = {}
        for row in rows:
            latest.setdefault(row.object_ref, row)
            first[row.object_ref] = row.id
        return [
            LoggedDecision(
                first[r.object_ref],
                r.object_ref,
                r.verdict,
                r.action,
                r.status,
                r.inputs,
                r.rules_version,
                r.decided_at,
            )
            for r in latest.values()
        ]
