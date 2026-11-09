"""Хранилище переносов лимита. Каждый запрос ограничен business_id."""

from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Text, Uuid, column, func, select, table, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.domain.transfers import TransferRecord, TransferState

TRANSFERS = table(
    "autopilot_transfers",
    column("id", Uuid),
    column("business_id", Uuid),
    column("account", Text),
    column("donor_id", Text),
    column("recipient_id", Text),
    column("donor_start", BigInteger),
    column("donor_target", BigInteger),
    column("recipient_target", BigInteger),
    column("amount", BigInteger),
    column("decision", JSONB),
    column("status", Text),
    column("created_at", DateTime(timezone=True)),
    column("updated_at", DateTime(timezone=True)),
)


def _record(row: Any) -> TransferRecord:
    return TransferRecord(
        id=str(row.id),
        account=row.account,
        donor_id=row.donor_id,
        recipient_id=row.recipient_id,
        donor_start=row.donor_start,
        donor_target=row.donor_target,
        recipient_target=row.recipient_target,
        amount=row.amount,
        decision=dict(row.decision),
        state=TransferState(row.status),
        created_at=row.created_at,
    )


class SqlTransferStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def add(self, record: TransferRecord) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                TRANSFERS.insert().values(
                    id=UUID(record.id),
                    business_id=self._business_id,
                    account=record.account,
                    donor_id=record.donor_id,
                    recipient_id=record.recipient_id,
                    donor_start=record.donor_start,
                    donor_target=record.donor_target,
                    recipient_target=record.recipient_target,
                    amount=record.amount,
                    decision=record.decision,
                    status=record.state.value,
                )
            )

    async def get(self, transfer_id: str) -> TransferRecord | None:
        statement = select(TRANSFERS).where(
            TRANSFERS.c.business_id == self._business_id, TRANSFERS.c.id == UUID(transfer_id)
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).one_or_none()
        return _record(row) if row else None

    async def open_transfers(self) -> list[TransferRecord]:
        statement = (
            select(TRANSFERS)
            .where(
                TRANSFERS.c.business_id == self._business_id,
                TRANSFERS.c.status == TransferState.OPEN.value,
            )
            .order_by(TRANSFERS.c.created_at)
        )
        async with self._sessions() as session:
            return [_record(row) for row in await session.execute(statement)]

    async def set_state(self, transfer_id: str, state: TransferState) -> None:
        statement = (
            update(TRANSFERS)
            .where(
                TRANSFERS.c.business_id == self._business_id,
                TRANSFERS.c.id == UUID(transfer_id),
            )
            .values(status=state.value, updated_at=func.now())
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)
