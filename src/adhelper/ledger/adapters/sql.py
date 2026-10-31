from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Index, Text, case, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.db import Base
from adhelper.ledger.domain.model import (
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)

OPEN_STATUSES = (ChangeStatus.PLANNED.value, ChangeStatus.APPLYING.value)
OPEN_CONDITION = text("status IN ('planned', 'applying')")


class ChangeRow(Base):
    __tablename__ = "changes"
    __table_args__ = (
        Index(
            "uq_changes_open_idempotency_key",
            "business_id",
            "idempotency_key",
            unique=True,
            postgresql_where=OPEN_CONDITION,
        ),
        Index("ix_changes_business_id_account_status", "business_id", "account", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID]
    account: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    decision_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    target_ref: Mapped[str] = mapped_column(Text)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB)
    planned: Mapped[dict[str, Any]] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    reverts_change_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _to_change(row: ChangeRow) -> Change:
    return Change(
        id=row.id,
        account=row.account,
        source=ChangeSource(row.source),
        kind=ChangeKind(row.kind),
        target_ref=row.target_ref,
        before=row.before,
        planned=row.planned,
        after=row.after,
        status=ChangeStatus(row.status),
        explanation=row.explanation,
        attempts=row.attempts,
        failure_reason=row.failure_reason,
        decision_id=row.decision_id,
        reverts_change_id=row.reverts_change_id,
    )


class SqlChangeRepository:
    """Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def add(
        self,
        proposed: ProposedChange,
        before: Mapping[str, Any],
        status: ChangeStatus,
        reason: str | None = None,
    ) -> Change:
        statement = (
            insert(ChangeRow)
            .values(
                business_id=self._business_id,
                account=proposed.account,
                source=proposed.source.value,
                decision_id=proposed.decision_id,
                kind=proposed.kind.value,
                target_ref=proposed.target_ref,
                before=dict(before),
                planned=dict(proposed.planned),
                status=status.value,
                explanation=proposed.explanation,
                reverts_change_id=proposed.reverts_change_id,
                idempotency_key=proposed.idempotency_key,
                failure_reason=reason,
            )
            .on_conflict_do_nothing(
                index_elements=["business_id", "idempotency_key"],
                index_where=OPEN_CONDITION,
            )
            .returning(ChangeRow)
        )
        async with self._sessions.begin() as session:
            row = (await session.scalars(statement)).one_or_none()
            if row is None:
                row = (
                    await session.scalars(
                        select(ChangeRow).where(
                            ChangeRow.business_id == self._business_id,
                            ChangeRow.idempotency_key == proposed.idempotency_key,
                            ChangeRow.status.in_(OPEN_STATUSES),
                        )
                    )
                ).one()
            return _to_change(row)

    async def pending(self, account: str) -> Sequence[Change]:
        # Остановка идёт первой: контроль бюджета важнее очереди других изменений.
        pause_first = case((ChangeRow.kind == ChangeKind.PAUSE.value, 0), else_=1)
        statement = (
            select(ChangeRow)
            .where(
                ChangeRow.business_id == self._business_id,
                ChangeRow.account == account,
                ChangeRow.status.in_(OPEN_STATUSES),
            )
            .order_by(pause_first, ChangeRow.id)
        )
        async with self._sessions() as session:
            return [_to_change(row) for row in await session.scalars(statement)]

    async def status_of(self, change_id: int) -> ChangeStatus | None:
        statement = select(ChangeRow.status).where(
            ChangeRow.id == change_id, ChangeRow.business_id == self._business_id
        )
        async with self._sessions() as session:
            status = await session.scalar(statement)
        return ChangeStatus(status) if status else None

    async def get(self, change_id: int) -> Change | None:
        statement = select(ChangeRow).where(
            ChangeRow.id == change_id, ChangeRow.business_id == self._business_id
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).one_or_none()
        return _to_change(row) if row else None

    async def on_target(self, account: str, target_ref: str) -> Sequence[Change]:
        statement = (
            select(ChangeRow)
            .where(
                ChangeRow.business_id == self._business_id,
                ChangeRow.account == account,
                ChangeRow.target_ref == target_ref,
            )
            .order_by(ChangeRow.id)
        )
        async with self._sessions() as session:
            return [_to_change(row) for row in await session.scalars(statement)]

    async def all(self) -> Sequence[Change]:
        statement = (
            select(ChangeRow)
            .where(ChangeRow.business_id == self._business_id)
            .order_by(ChangeRow.id)
        )
        async with self._sessions() as session:
            return [_to_change(row) for row in await session.scalars(statement)]

    async def mark_applying(self, change: Change) -> Change:
        return await self._update(
            change, status=ChangeStatus.APPLYING.value, attempts=ChangeRow.attempts + 1
        )

    async def finish(
        self,
        change: Change,
        status: ChangeStatus,
        after: Mapping[str, Any] | None,
        reason: str | None = None,
    ) -> Change:
        return await self._update(
            change,
            status=status.value,
            after=dict(after) if after is not None else None,
            failure_reason=reason,
        )

    async def _update(self, change: Change, **values: Any) -> Change:
        statement = (
            update(ChangeRow)
            .where(ChangeRow.business_id == self._business_id, ChangeRow.id == change.id)
            .values(**values)
            .returning(ChangeRow)
        )
        async with self._sessions.begin() as session:
            return _to_change((await session.scalars(statement)).one())


async def accounts_with_open_changes(
    sessions: async_sessionmaker[AsyncSession],
) -> list[tuple[UUID, str]]:
    """Единственный запрос поверх всех бизнесов: только для фонового согласования."""
    statement = (
        select(ChangeRow.business_id, ChangeRow.account)
        .where(ChangeRow.status.in_(OPEN_STATUSES))
        .distinct()
    )
    async with sessions() as session:
        return [(row.business_id, row.account) for row in await session.execute(statement)]


class PostgresAccountLock:
    """Блокировка сессии: при падении процесса PostgreSQL снимает её сам."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def _hold(self, account: str) -> AsyncIterator[bool]:
        key = {"account": account}
        connection = await self._engine.connect()
        try:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:account, 0))"), key
            )
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:account, 0))"), key
                    )
        finally:
            await connection.close()

    def hold(self, account: str) -> Any:
        return self._hold(account)
