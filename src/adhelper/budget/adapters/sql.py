from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.budget.domain.balance import BalanceBaseline
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.ports import PeriodRecord
from adhelper.budget.domain.watch import SpendSnapshot
from adhelper.db import Base


class BudgetPeriodRow(Base):
    __tablename__ = "budget_periods"
    __table_args__ = (Index("ix_budget_periods_state_next_check_at", "state", "next_check_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID]
    account: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hard_limit_micros: Mapped[int] = mapped_column(BigInteger)
    reserve_bp: Mapped[int]
    rules_version: Mapped[str] = mapped_column(Text)
    opening_balance_micros: Mapped[int | None] = mapped_column(BigInteger)
    opening_spent_micros: Mapped[int | None] = mapped_column(BigInteger)
    last_balance_micros: Mapped[int | None] = mapped_column(BigInteger)
    balance_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    stop_pending: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SpendSnapshotRow(Base):
    __tablename__ = "spend_snapshots"
    __table_args__ = (
        Index("uq_spend_snapshots_period_id_data_as_of", "period_id", "data_as_of", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    period_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("budget_periods.id"))
    reported_micros: Mapped[int] = mapped_column(BigInteger)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    measured_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _to_record(row: BudgetPeriodRow) -> PeriodRecord:
    return PeriodRecord(
        id=row.id,
        business_id=row.business_id,
        account=row.account,
        period=BudgetPeriod(row.hard_limit_micros, row.reserve_bp, row.starts_at, row.ends_at),
        rules_version=row.rules_version,
        opening_balance=row.opening_balance_micros,
        stop_pending=row.stop_pending,
        opening_spent=row.opening_spent_micros,
        last_balance=row.last_balance_micros,
        balance_checked_at=row.balance_checked_at,
    )


class SqlPeriodStore:
    """Хранилище для фоновых задач, поэтому работает поверх всех бизнесов.

    Запросы для пользователя так строить нельзя: они пойдут через репозиторий с business_id.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(
        self,
        business_id: UUID,
        account: str,
        period: BudgetPeriod,
        rules_version: str,
        opening_balance: int | None,
        next_check_at: datetime,
    ) -> int:
        if period.starts_at is None or period.ends_at is None:
            raise ValueError("У сохраняемого периода должны быть даты")
        row = BudgetPeriodRow(
            business_id=business_id,
            account=account,
            starts_at=period.starts_at,
            ends_at=period.ends_at,
            hard_limit_micros=period.hard_limit,
            reserve_bp=period.reserve_bp,
            rules_version=rules_version,
            opening_balance_micros=opening_balance,
            next_check_at=next_check_at,
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            return row.id

    async def due(self, now: datetime) -> Sequence[PeriodRecord]:
        statement = (
            select(BudgetPeriodRow)
            .where(BudgetPeriodRow.state == "active", BudgetPeriodRow.next_check_at <= now)
            .order_by(BudgetPeriodRow.next_check_at)
        )
        async with self._sessions() as session:
            return [_to_record(row) for row in await session.scalars(statement)]

    async def active_for(
        self, business_id: UUID, account: str, now: datetime
    ) -> PeriodRecord | None:
        statement = (
            select(BudgetPeriodRow)
            .where(
                BudgetPeriodRow.business_id == business_id,
                BudgetPeriodRow.account == account,
                BudgetPeriodRow.state == "active",
                BudgetPeriodRow.starts_at <= now,
            )
            .order_by(BudgetPeriodRow.starts_at.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).one_or_none()
            return _to_record(row) if row else None

    async def recent_snapshots(self, period_id: int) -> Sequence[SpendSnapshot]:
        """Два последних снимка с разным моментом данных, от старого к новому."""
        statement = (
            select(SpendSnapshotRow)
            .where(SpendSnapshotRow.period_id == period_id)
            .order_by(SpendSnapshotRow.data_as_of.desc())
            .limit(2)
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
        return [SpendSnapshot(r.reported_micros, r.data_as_of) for r in reversed(rows)]

    async def save_cycle(
        self,
        period_id: int,
        snapshot: SpendSnapshot | None,
        stop_pending: bool,
        next_check_at: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            if snapshot is not None:
                await session.execute(
                    insert(SpendSnapshotRow)
                    .values(
                        period_id=period_id,
                        reported_micros=snapshot.reported,
                        data_as_of=snapshot.data_as_of,
                    )
                    .on_conflict_do_nothing(index_elements=["period_id", "data_as_of"])
                )
            await session.execute(
                update(BudgetPeriodRow)
                .where(BudgetPeriodRow.id == period_id)
                .values(stop_pending=stop_pending, next_check_at=next_check_at)
            )

    async def reschedule(self, period_id: int, next_check_at: datetime) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(BudgetPeriodRow)
                .where(BudgetPeriodRow.id == period_id)
                .values(next_check_at=next_check_at)
            )

    async def save_balance(
        self,
        period_id: int,
        balance: int,
        checked_at: datetime,
        baseline: BalanceBaseline | None,
    ) -> None:
        values: dict[str, object] = {
            "last_balance_micros": balance,
            "balance_checked_at": checked_at,
        }
        if baseline is not None:
            values["opening_balance_micros"] = baseline.balance
            values["opening_spent_micros"] = baseline.spent
        async with self._sessions.begin() as session:
            await session.execute(
                update(BudgetPeriodRow).where(BudgetPeriodRow.id == period_id).values(**values)
            )

    async def set_hard_limit(self, period_id: int, hard_limit: int) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(BudgetPeriodRow)
                .where(BudgetPeriodRow.id == period_id)
                .values(hard_limit_micros=hard_limit)
            )

    async def latest_spent(self, period_id: int) -> int | None:
        snapshots = await self.recent_snapshots(period_id)
        return snapshots[-1].reported if snapshots else None
