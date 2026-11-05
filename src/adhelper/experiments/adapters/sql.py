from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Index, Text, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.db import Base
from adhelper.experiments.domain.model import ExperimentRecord, Status, TestAlreadyRunning

ONE_TEST_INDEX = "uq_experiments_open_business_campaign"
OPEN = (Status.REVIEW.value, Status.RUNNING.value, Status.PAUSED.value)


class ExperimentRow(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index(
            ONE_TEST_INDEX,
            "business_id",
            "campaign_id",
            unique=True,
            postgresql_where=text("status IN ('review', 'running', 'paused')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID]
    account: Mapped[str] = mapped_column(Text)
    campaign_id: Mapped[str] = mapped_column(Text)
    control_ref: Mapped[str] = mapped_column(Text)
    # Пусто, пока вариант не написан и не создан на площадке.
    variant_ref: Mapped[str | None] = mapped_column(Text)
    hypothesis: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text, server_default=text("'A'"))
    switch_to: Mapped[str | None] = mapped_column(Text)
    switch_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    winner: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Когда тест получил итог: по нему «Сегодня» показывает карточку итога неделю.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _record(row: ExperimentRow) -> ExperimentRecord:
    return ExperimentRecord(
        row.id,
        row.account,
        row.campaign_id,
        row.control_ref,
        row.variant_ref,
        row.hypothesis,
        Status(row.status),
        row.reason,
        row.expected,
        row.switch_to,
        row.switch_seq,
        row.winner,
        row.explanation,
        row.rules_version,
        row.started_at,
        row.finished_at,
    )


class SqlExperimentStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def add(
        self,
        account: str,
        campaign_id: str,
        control_ref: str,
        variant_ref: str | None,
        hypothesis: str,
        rules_version: str,
    ) -> int:
        row = ExperimentRow(
            business_id=self._business_id,
            account=account,
            campaign_id=campaign_id,
            control_ref=control_ref,
            variant_ref=variant_ref,
            hypothesis=hypothesis,
            status=Status.REVIEW.value,
            rules_version=rules_version,
        )
        try:
            async with self._sessions.begin() as session:
                session.add(row)
                await session.flush()
                return row.id
        except IntegrityError as error:
            if ONE_TEST_INDEX in str(error.orig):
                raise TestAlreadyRunning from None
            raise

    async def open_for(self, account: str) -> list[ExperimentRecord]:
        """Тесты, которые ещё идут или готовятся."""
        statement = (
            select(ExperimentRow)
            .where(
                ExperimentRow.business_id == self._business_id,
                ExperimentRow.account == account,
                ExperimentRow.status.in_(OPEN),
            )
            .order_by(ExperimentRow.id)
        )
        async with self._sessions() as session:
            return [_record(row) for row in await session.scalars(statement)]

    async def tried(self, account: str, control_ref: str) -> set[str]:
        statement = select(ExperimentRow.hypothesis).where(
            ExperimentRow.business_id == self._business_id,
            ExperimentRow.account == account,
            ExperimentRow.control_ref == control_ref,
        )
        async with self._sessions() as session:
            return set(await session.scalars(statement))

    async def get(self, experiment_id: int) -> ExperimentRecord | None:
        statement = select(ExperimentRow).where(
            ExperimentRow.id == experiment_id, ExperimentRow.business_id == self._business_id
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).one_or_none()
        return _record(row) if row else None

    async def _update(self, experiment_id: int, **values: Any) -> None:
        statement = (
            update(ExperimentRow)
            .where(
                ExperimentRow.id == experiment_id, ExperimentRow.business_id == self._business_id
            )
            .values(**values)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def set_status(
        self,
        experiment_id: int,
        status: Status,
        reason: str | None = None,
        started_at: datetime | None = None,
        winner: str | None = None,
        explanation: str | None = None,
        finished_at: datetime | None = None,
        rules_version: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status.value, "reason": reason}
        if started_at is not None:
            values["started_at"] = started_at
        if winner is not None:
            values["winner"] = winner
            values["explanation"] = explanation
        if finished_at is not None:
            values["finished_at"] = finished_at
        if rules_version is not None:
            # Итог решили эти правила: тест мог начаться на прежней версии.
            values["rules_version"] = rules_version
        await self._update(experiment_id, **values)

    async def set_variant(self, experiment_id: int, variant_ref: str) -> None:
        """Вариант создан на площадке и ждёт проверки выключенным."""
        await self._update(experiment_id, variant_ref=variant_ref)

    async def begin_switch(self, experiment_id: int, target: str) -> None:
        await self._update(experiment_id, switch_to=target, switch_seq=ExperimentRow.switch_seq + 1)

    async def finish_switch(self, experiment_id: int, target: str) -> None:
        await self._update(experiment_id, expected=target, switch_to=None)
