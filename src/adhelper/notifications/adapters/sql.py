from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    bindparam,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.db import Base
from adhelper.notifications.domain.model import SENDING_LEASE, Channel, Delivery

OPEN_ALERT = text("resolved_at IS NULL")
WAITING = text("status IN ('pending', 'sending')")


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


class DeliveryRow(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("uq_notification_deliveries_alert_channel", "alert_id", "channel", unique=True),
        Index("ix_notification_deliveries_waiting", "next_attempt_at", postgresql_where=WAITING),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID]
    alert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alerts.id", name="fk_notification_deliveries_alert")
    )
    channel: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramLinkRow(Base):
    __tablename__ = "telegram_links"
    __table_args__ = (Index("uq_telegram_links_code_hash", "code_hash", unique=True),)

    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    code_hash: Mapped[str | None] = mapped_column(Text)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramBotStateRow(Base):
    __tablename__ = "telegram_bot_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_telegram_bot_state_single"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    update_offset: Mapped[int] = mapped_column(BigInteger)


# Отправки создаются вместе с тревогой: адрес почты и привязанный чат владельца бизнеса.
CREATE_DELIVERIES = text(
    """
    INSERT INTO notification_deliveries (business_id, alert_id, channel)
    SELECT b.id, :alert_id, 'email' FROM businesses b JOIN users u ON u.id = b.user_id
     WHERE b.id = :business_id AND coalesce(u.email, '') <> ''
    UNION ALL
    SELECT b.id, :alert_id, 'telegram' FROM businesses b
      JOIN telegram_links t ON t.user_id = b.user_id
     WHERE b.id = :business_id AND t.chat_id IS NOT NULL
    """
)


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
            .returning(AlertRow.id)
        )
        async with self._sessions.begin() as session:
            alert_id = await session.scalar(statement)
            if alert_id is not None:
                await session.execute(
                    CREATE_DELIVERIES, {"alert_id": alert_id, "business_id": self._business_id}
                )

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


CLAIM = text(
    """
    WITH claimed AS (
        UPDATE notification_deliveries d
           SET status = 'sending', attempts = d.attempts + 1, next_attempt_at = :lease
         WHERE d.id IN (
               SELECT id FROM notification_deliveries
                WHERE status IN ('pending', 'sending') AND next_attempt_at <= :now
                  AND channel IN :channels
                ORDER BY id LIMIT :limit
                FOR UPDATE SKIP LOCKED)
        RETURNING d.id, d.business_id, d.alert_id, d.channel, d.attempts
    )
    SELECT c.id, c.business_id, c.channel, c.attempts, a.kind, a.details, a.created_at,
           a.resolved_at IS NOT NULL AS resolved, b.name AS business_name,
           CASE c.channel WHEN 'email' THEN nullif(u.email, '')
                          ELSE t.chat_id::text END AS address
      FROM claimed c
      JOIN alerts a ON a.id = c.alert_id AND a.business_id = c.business_id
      JOIN businesses b ON b.id = c.business_id
      JOIN users u ON u.id = b.user_id
      LEFT JOIN telegram_links t ON t.user_id = b.user_id
     ORDER BY c.id
    """
).bindparams(bindparam("channels", expanding=True))


class SqlOutbox:
    """Очередь отправок всех бизнесов: её разбирает воркер, как страж бюджета свои периоды."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(
        self, channels: Collection[Channel], now: datetime, limit: int
    ) -> Sequence[Delivery]:
        if not channels:
            return []
        params = {
            "now": now,
            "lease": now + SENDING_LEASE,
            "channels": [str(c) for c in channels],
            "limit": limit,
        }
        async with self._sessions.begin() as session:
            rows = (await session.execute(CLAIM, params)).mappings().all()
        return [
            Delivery(
                id=row["id"],
                business_id=row["business_id"],
                channel=Channel(row["channel"]),
                attempts=row["attempts"],
                kind=row["kind"],
                business_name=row["business_name"],
                raised_at=row["created_at"],
                resolved=row["resolved"],
                address=row["address"],
                details=row["details"],
            )
            for row in rows
        ]

    async def _finish(self, delivery_id: int, **values: Any) -> None:
        statement = (
            update(DeliveryRow)
            .where(DeliveryRow.id == delivery_id, DeliveryRow.status == "sending")
            .values(**values)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def sent(self, delivery_id: int, now: datetime) -> None:
        await self._finish(delivery_id, status="sent", sent_at=now, last_error=None)

    async def skipped(self, delivery_id: int, reason: str) -> None:
        await self._finish(delivery_id, status="skipped", last_error=reason)

    async def retry(self, delivery_id: int, at: datetime, error: str) -> None:
        await self._finish(delivery_id, status="pending", next_attempt_at=at, last_error=error)

    async def failed(self, delivery_id: int, error: str) -> None:
        await self._finish(delivery_id, status="failed", last_error=error)


class SqlTelegramLinks:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def start(self, user_id: UUID, code_hash: str, expires_at: datetime) -> None:
        statement = insert(TelegramLinkRow).values(
            user_id=user_id, code_hash=code_hash, code_expires_at=expires_at
        )
        statement = statement.on_conflict_do_update(
            index_elements=[TelegramLinkRow.user_id],
            set_={"code_hash": code_hash, "code_expires_at": expires_at},
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def finish(self, code_hash: str, chat_id: int, now: datetime) -> bool:
        statement = (
            update(TelegramLinkRow)
            .where(TelegramLinkRow.code_hash == code_hash, TelegramLinkRow.code_expires_at > now)
            .values(chat_id=chat_id, linked_at=now, code_hash=None, code_expires_at=None)
            .returning(TelegramLinkRow.user_id)
        )
        async with self._sessions.begin() as session:
            return await session.scalar(statement) is not None

    async def unlink(self, user_id: UUID) -> None:
        statement = (
            update(TelegramLinkRow)
            .where(TelegramLinkRow.user_id == user_id)
            .values(chat_id=None, linked_at=None, code_hash=None, code_expires_at=None)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def linked(self, user_id: UUID) -> bool:
        statement = select(TelegramLinkRow.chat_id).where(TelegramLinkRow.user_id == user_id)
        async with self._sessions() as session:
            return await session.scalar(statement) is not None

    async def offset(self) -> int | None:
        statement = select(TelegramBotStateRow.update_offset)
        async with self._sessions() as session:
            offset: int | None = await session.scalar(statement)
            return offset

    async def save_offset(self, offset: int) -> None:
        statement = insert(TelegramBotStateRow).values(id=1, update_offset=offset)
        statement = statement.on_conflict_do_update(
            index_elements=[TelegramBotStateRow.id], set_={"update_offset": offset}
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)
