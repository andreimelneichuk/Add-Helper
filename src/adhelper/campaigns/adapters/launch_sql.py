from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.budget.adapters.sql import BudgetPeriodRow
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.campaigns.domain.ports import ActivePeriodExists, StoredLaunch
from adhelper.db import Base

# Транзакционная блокировка на бизнес: два одновременных запуска не создадут два периода.
LAUNCH_LOCK = 0x5173_0003


class LaunchRow(Base):
    __tablename__ = "launches"
    __table_args__ = (UniqueConstraint("business_id", name="uq_launches_business_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", name="fk_launches_business")
    )
    account: Mapped[str] = mapped_column(Text)
    period_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("budget_periods.id", name="fk_launches_period")
    )
    rules_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _stored(row: LaunchRow, period: BudgetPeriodRow) -> StoredLaunch:
    return StoredLaunch(
        id=row.id,
        account=row.account,
        period=BudgetPeriod(
            period.hard_limit_micros, period.reserve_bp, period.starts_at, period.ends_at
        ),
        rules_version=row.rules_version,
    )


class SqlLaunches:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def _find(self, session: AsyncSession) -> StoredLaunch | None:
        statement = (
            select(LaunchRow, BudgetPeriodRow)
            .join(BudgetPeriodRow, BudgetPeriodRow.id == LaunchRow.period_id)
            .where(LaunchRow.business_id == self._business_id)
        )
        found = (await session.execute(statement)).one_or_none()
        return _stored(found[0], found[1]) if found else None

    async def get(self) -> StoredLaunch | None:
        async with self._sessions() as session:
            return await self._find(session)

    async def start(
        self,
        account: str,
        period: BudgetPeriod,
        rules_version: str,
        budget_rules_version: str,
        now: datetime,
    ) -> StoredLaunch:
        if period.starts_at is None or period.ends_at is None:
            raise ValueError("У периода запуска должны быть даты")
        async with self._sessions.begin() as session:
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(LAUNCH_LOCK, func.hashtext(str(self._business_id)))
                )
            )
            existing = await self._find(session)
            if existing is not None:
                return existing
            running = await session.scalar(
                select(BudgetPeriodRow.id).where(
                    BudgetPeriodRow.business_id == self._business_id,
                    BudgetPeriodRow.account == account,
                    BudgetPeriodRow.state == "active",
                    BudgetPeriodRow.ends_at > now,
                )
            )
            if running is not None:
                raise ActivePeriodExists
            budget = BudgetPeriodRow(
                business_id=self._business_id,
                account=account,
                starts_at=period.starts_at,
                ends_at=period.ends_at,
                hard_limit_micros=period.hard_limit,
                reserve_bp=period.reserve_bp,
                rules_version=budget_rules_version,
                opening_balance_micros=None,
                # Страж смотрит период сразу: кампании начнут тратить после проверки Яндекса.
                next_check_at=now,
            )
            session.add(budget)
            await session.flush()
            row = LaunchRow(
                business_id=self._business_id,
                account=account,
                period_id=budget.id,
                rules_version=rules_version,
            )
            session.add(row)
            await session.flush()
            return _stored(row, budget)
