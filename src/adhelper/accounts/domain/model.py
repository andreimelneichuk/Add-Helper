from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


def token_context(business_id: UUID, login: str, kind: str) -> str:
    """Контекст шифрования токена привязан к бизнесу и логину кабинета."""
    return f"ad_account:{business_id}:{login}:{kind}"


class AccountState(StrEnum):
    CONNECTED = "connected"
    NEEDS_RECONNECT = "needs_reconnect"
    DISCONNECTED = "disconnected"


class FlowPurpose(StrEnum):
    LOGIN = "login"
    CONNECT = "connect"


@dataclass(frozen=True)
class User:
    id: UUID
    yandex_uid: str
    email: str | None


@dataclass(frozen=True)
class Business:
    id: UUID
    user_id: UUID
    name: str


@dataclass(frozen=True)
class AdAccount:
    """Кабинет без токенов: этот объект можно показывать и логировать."""

    id: UUID
    business_id: UUID
    platform: str
    login: str
    scopes: tuple[str, ...]
    state: AccountState
    last_balance_micros: int | None = None
    balance_checked_at: datetime | None = None


@dataclass(frozen=True)
class ExternalIdentity:
    uid: str
    login: str
    email: str | None


@dataclass(frozen=True)
class OAuthTokens:
    access: str
    refresh: str
    expires_at: datetime
    scopes: tuple[str, ...]

    def __repr__(self) -> str:
        return f"OAuthTokens(expires_at={self.expires_at!r}, scopes={self.scopes!r})"


@dataclass(frozen=True)
class OAuthFlow:
    state_hash: str
    purpose: FlowPurpose
    encrypted_verifier: str
    expires_at: datetime
    user_id: UUID | None = None
    business_id: UUID | None = None


@dataclass(frozen=True)
class SessionInfo:
    user: User
    csrf_token: str


@dataclass(frozen=True)
class StoredTokens:
    account_id: UUID
    login: str
    encrypted_access: str
    encrypted_refresh: str
    expires_at: datetime
    version: int
