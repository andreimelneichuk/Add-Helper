from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.accounts.adapters.crypto import DecryptionFailed
from adhelper.accounts.domain.marking import AdvertiserKind, Marking
from adhelper.accounts.domain.model import (
    AccountState,
    AdAccount,
    Business,
    ExternalIdentity,
    FlowPurpose,
    OAuthFlow,
    OAuthTokens,
    SessionInfo,
    StoredTokens,
    User,
)
from adhelper.accounts.domain.ports import AccountTaken, TokenCipher
from adhelper.db import Base

ACTIVE_ACCOUNT_INDEX = "uq_ad_accounts_active_platform_login"


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    yandex_uid: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BusinessRow(Base):
    __tablename__ = "businesses"
    __table_args__ = (Index("ix_businesses_user_id", "user_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdAccountRow(Base):
    __tablename__ = "ad_accounts"
    __table_args__ = (
        UniqueConstraint("business_id", "external_login", name="uq_ad_accounts_business_login"),
        Index(
            ACTIVE_ACCOUNT_INDEX,
            "platform",
            "external_login",
            unique=True,
            postgresql_where=text("state <> 'disconnected'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id"))
    platform: Mapped[str] = mapped_column(Text)
    external_login: Mapped[str] = mapped_column(Text)
    encrypted_access: Mapped[str] = mapped_column(Text)
    encrypted_refresh: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    state: Mapped[str] = mapped_column(Text)


class SessionRow(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    csrf_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthFlowRow(Base):
    __tablename__ = "oauth_flows"

    state_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    purpose: Mapped[str] = mapped_column(Text)
    encrypted_verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    business_id: Mapped[UUID | None] = mapped_column(ForeignKey("businesses.id"))


def _user(row: UserRow) -> User:
    return User(row.id, row.yandex_uid, row.email)


def _business(row: BusinessRow) -> Business:
    return Business(row.id, row.user_id, row.name)


def _account(row: AdAccountRow) -> AdAccount:
    return AdAccount(
        row.id,
        row.business_id,
        row.platform,
        row.external_login,
        tuple(row.scopes),
        AccountState(row.state),
    )


class SqlIdentityStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save_flow(self, flow: OAuthFlow) -> None:
        async with self._sessions.begin() as session:
            session.add(
                OAuthFlowRow(
                    state_hash=flow.state_hash,
                    purpose=flow.purpose.value,
                    encrypted_verifier=flow.encrypted_verifier,
                    expires_at=flow.expires_at,
                    user_id=flow.user_id,
                    business_id=flow.business_id,
                )
            )

    async def consume_flow(self, state_hash: str, now: datetime) -> OAuthFlow | None:
        """Одним запросом, чтобы два одновременных ответа не прошли по одному state."""
        statement = (
            update(OAuthFlowRow)
            .where(
                OAuthFlowRow.state_hash == state_hash,
                OAuthFlowRow.used_at.is_(None),
                OAuthFlowRow.expires_at > now,
            )
            .values(used_at=now)
            .returning(OAuthFlowRow)
        )
        async with self._sessions.begin() as session:
            row = (await session.scalars(statement)).one_or_none()
            if row is None:
                return None
            return OAuthFlow(
                row.state_hash,
                FlowPurpose(row.purpose),
                row.encrypted_verifier,
                row.expires_at,
                row.user_id,
                row.business_id,
            )

    async def upsert_user(self, identity: ExternalIdentity) -> User:
        statement = (
            insert(UserRow)
            .values(id=uuid4(), yandex_uid=identity.uid, email=identity.email)
            .on_conflict_do_update(index_elements=["yandex_uid"], set_={"email": identity.email})
            .returning(UserRow)
        )
        async with self._sessions.begin() as session:
            return _user((await session.scalars(statement)).one())

    async def create_session(
        self, user_id: UUID, token_hash: str, csrf_token: str, expires_at: datetime
    ) -> None:
        async with self._sessions.begin() as session:
            session.add(
                SessionRow(
                    token_hash=token_hash,
                    user_id=user_id,
                    csrf_token=csrf_token,
                    expires_at=expires_at,
                )
            )

    async def find_session(self, token_hash: str, now: datetime) -> SessionInfo | None:
        statement = (
            select(SessionRow, UserRow)
            .join(UserRow, UserRow.id == SessionRow.user_id)
            .where(
                SessionRow.token_hash == token_hash,
                SessionRow.revoked_at.is_(None),
                SessionRow.expires_at > now,
            )
        )
        async with self._sessions() as session:
            found = (await session.execute(statement)).one_or_none()
        return SessionInfo(_user(found[1]), found[0].csrf_token) if found else None

    async def revoke_session(self, token_hash: str, now: datetime) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(SessionRow).where(SessionRow.token_hash == token_hash).values(revoked_at=now)
            )


class SqlUserScope:
    """Все запросы ограничены пользователем; чужой бизнес неотличим от несуществующего."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], user_id: UUID) -> None:
        self._sessions = sessions
        self._user_id = user_id

    async def create_business(self, name: str) -> Business:
        row = BusinessRow(id=uuid4(), user_id=self._user_id, name=name)
        async with self._sessions.begin() as session:
            session.add(row)
        return _business(row)

    async def businesses(self) -> Sequence[Business]:
        statement = (
            select(BusinessRow)
            .where(
                BusinessRow.user_id == self._user_id,
                BusinessRow.deleted_at.is_(None),
            )
            .order_by(BusinessRow.created_at)
        )
        async with self._sessions() as session:
            return [_business(row) for row in await session.scalars(statement)]

    async def business(self, business_id: UUID) -> Business | None:
        statement = select(BusinessRow).where(
            BusinessRow.id == business_id,
            BusinessRow.user_id == self._user_id,
            BusinessRow.deleted_at.is_(None),
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).one_or_none()
        return _business(row) if row else None

    async def accounts(self, business_id: UUID) -> Sequence[AdAccount]:
        statement = (
            select(AdAccountRow)
            .join(BusinessRow, BusinessRow.id == AdAccountRow.business_id)
            .where(
                AdAccountRow.business_id == business_id,
                BusinessRow.user_id == self._user_id,
                BusinessRow.deleted_at.is_(None),
            )
            .order_by(AdAccountRow.external_login)
        )
        async with self._sessions() as session:
            return [_account(row) for row in await session.scalars(statement)]

    async def delete_business(self, business_id: UUID) -> bool:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            statement = select(BusinessRow).where(
                BusinessRow.id == business_id,
                BusinessRow.user_id == self._user_id,
                BusinessRow.deleted_at.is_(None),
            )
            row = (await session.scalars(statement)).one_or_none()
            if row is None:
                return False
            row.deleted_at = now
            # Переводим привязанные кабинеты в disconnected:
            # это освобождает uq_ad_accounts_active_platform_login
            await session.execute(
                update(AdAccountRow)
                .where(AdAccountRow.business_id == business_id)
                .values(state=AccountState.DISCONNECTED.value)
            )
            # Останавливаем активные периоды бюджета
            await session.execute(
                text(
                    "UPDATE budget_periods SET state = 'stopped' "
                    "WHERE business_id = :b AND state = 'active'"
                ),
                {"b": business_id},
            )
            # Закрываем открытые тревоги
            await session.execute(
                text(
                    "UPDATE alerts SET resolved_at = :now "
                    "WHERE business_id = :b AND resolved_at IS NULL"
                ),
                {"b": business_id, "now": now},
            )
            # Отменяем висящие в очереди уведомления
            await session.execute(
                text(
                    "UPDATE notification_deliveries SET status = 'skipped' "
                    "WHERE business_id = :b AND status = 'pending'"
                ),
                {"b": business_id},
            )
            return True


class SqlAccountTokens:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def save_connected(
        self,
        login: str,
        platform: str,
        encrypted_access: str,
        encrypted_refresh: str,
        tokens: OAuthTokens,
    ) -> AdAccount:
        fields = {
            "encrypted_access": encrypted_access,
            "encrypted_refresh": encrypted_refresh,
            "token_expires_at": tokens.expires_at,
            "scopes": list(tokens.scopes),
            "state": AccountState.CONNECTED.value,
        }
        statement = (
            insert(AdAccountRow)
            .values(
                id=uuid4(),
                business_id=self._business_id,
                platform=platform,
                external_login=login,
                **fields,
            )
            .on_conflict_do_update(
                constraint="uq_ad_accounts_business_login",
                set_={**fields, "token_version": AdAccountRow.token_version + 1},
            )
            .returning(AdAccountRow)
        )
        try:
            async with self._sessions.begin() as session:
                return _account((await session.scalars(statement)).one())
        except IntegrityError as error:
            if ACTIVE_ACCOUNT_INDEX in str(error.orig):
                raise AccountTaken from None
            raise

    async def load(self, login: str) -> StoredTokens | None:
        statement = select(AdAccountRow).where(
            AdAccountRow.business_id == self._business_id, AdAccountRow.external_login == login
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).one_or_none()
        if row is None:
            return None
        return StoredTokens(
            row.id,
            row.external_login,
            row.encrypted_access,
            row.encrypted_refresh,
            row.token_expires_at,
            row.token_version,
        )

    async def replace(
        self,
        stored: StoredTokens,
        encrypted_access: str,
        encrypted_refresh: str,
        expires_at: datetime,
    ) -> bool:
        """False, если другой процесс обновил токены раньше."""
        statement = (
            update(AdAccountRow)
            .where(
                AdAccountRow.id == stored.account_id,
                AdAccountRow.business_id == self._business_id,
                AdAccountRow.token_version == stored.version,
            )
            .values(
                encrypted_access=encrypted_access,
                encrypted_refresh=encrypted_refresh,
                token_expires_at=expires_at,
                token_version=stored.version + 1,
            )
            .returning(AdAccountRow.id)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(statement)).first() is not None

    async def mark_needs_reconnect(self, login: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(AdAccountRow)
                .where(
                    AdAccountRow.business_id == self._business_id,
                    AdAccountRow.external_login == login,
                )
                .values(state=AccountState.NEEDS_RECONNECT.value)
            )


async def logins_of_business(
    sessions: async_sessionmaker[AsyncSession], business_id: UUID
) -> list[str]:
    statement = (
        select(AdAccountRow.external_login)
        .where(AdAccountRow.business_id == business_id)
        .order_by(AdAccountRow.external_login)
    )
    async with sessions() as session:
        return list(await session.scalars(statement))


class MarkingRow(Base):
    __tablename__ = "advertiser_marking"

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    encrypted_inn: Mapped[str] = mapped_column(Text)
    encrypted_name: Mapped[str] = mapped_column(Text)
    encrypted_phone: Mapped[str | None] = mapped_column(Text)
    okved: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def marking_context(business_id: UUID, field: str) -> str:
    return f"marking:{business_id}:{field}"


class SqlMarking:
    """Маркировка одного бизнеса. Единственное место, где ИНН расшифровывается."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], business_id: UUID, cipher: TokenCipher
    ) -> None:
        self._sessions = sessions
        self._business_id = business_id
        self._cipher = cipher

    def _seal(self, value: str, field: str) -> str:
        return self._cipher.encrypt(value, marking_context(self._business_id, field))

    def _open(self, value: str, field: str) -> str:
        return self._cipher.decrypt(value, marking_context(self._business_id, field))

    async def get(self) -> Marking | None:
        async with self._sessions() as session:
            row = await session.get(MarkingRow, self._business_id)
        if row is None:
            return None
        try:
            return Marking(
                AdvertiserKind(row.kind),
                self._open(row.encrypted_inn, "inn"),
                self._open(row.encrypted_name, "name"),
                None if row.encrypted_phone is None else self._open(row.encrypted_phone, "phone"),
                row.okved,
            )
        except DecryptionFailed:
            # Чужой шифртекст или потерянный ключ: данных нет, владелец введёт их заново.
            return None

    async def save(self, marking: Marking) -> None:
        fields = {
            "kind": marking.kind.value,
            "encrypted_inn": self._seal(marking.inn, "inn"),
            "encrypted_name": self._seal(marking.name, "name"),
            "encrypted_phone": None
            if marking.phone is None
            else self._seal(marking.phone, "phone"),
            "okved": marking.okved,
            "updated_at": func.now(),
        }
        statement = (
            insert(MarkingRow)
            .values(business_id=self._business_id, **fields)
            .on_conflict_do_update(index_elements=[MarkingRow.business_id], set_=fields)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)


async def account_states(
    sessions: async_sessionmaker[AsyncSession], business_id: UUID
) -> list[tuple[str, AccountState]]:
    """Кабинеты бизнеса и их состояние: для проверки готовности к запуску."""
    statement = (
        select(AdAccountRow.external_login, AdAccountRow.state)
        .where(AdAccountRow.business_id == business_id)
        .order_by(AdAccountRow.external_login)
    )
    async with sessions() as session:
        return [(login, AccountState(state)) for login, state in await session.execute(statement)]
