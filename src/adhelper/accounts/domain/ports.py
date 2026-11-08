from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from adhelper.accounts.domain.model import (
    AdAccount,
    Business,
    ExternalIdentity,
    OAuthFlow,
    OAuthTokens,
    SessionInfo,
    StoredTokens,
    User,
)


class ProviderUnavailable(Exception):
    """Провайдер не ответил: токены остаются прежними, можно повторить."""


class GrantRejected(Exception):
    """Провайдер отверг код или токен обновления: нужен новый вход."""


class AccountTaken(Exception):
    """Кабинет уже подключён к другому бизнесу: два стража на один кабинет недопустимы."""


class DecryptionFailed(Exception):
    """Шифртекст повреждён, контекст не совпал или нет подходящего ключа."""


class OAuthProvider(Protocol):
    def authorize_url(
        self, state: str, code_challenge: str, redirect_uri: str, scopes: Sequence[str]
    ) -> str: ...
    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> OAuthTokens: ...
    async def refresh(self, refresh_token: str) -> OAuthTokens: ...
    async def identity(self, access_token: str) -> ExternalIdentity: ...


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str, context: str) -> str: ...
    def decrypt(self, ciphertext: str, context: str) -> str: ...


class IdentityStore(Protocol):
    """Вход работает до появления пользователя, поэтому без ограничения по user_id."""

    async def save_flow(self, flow: OAuthFlow) -> None: ...
    async def consume_flow(self, state_hash: str, now: datetime) -> OAuthFlow | None: ...
    async def upsert_user(self, identity: ExternalIdentity) -> User: ...
    async def create_session(
        self, user_id: UUID, token_hash: str, csrf_token: str, expires_at: datetime
    ) -> None: ...
    async def find_session(self, token_hash: str, now: datetime) -> SessionInfo | None: ...
    async def revoke_session(self, token_hash: str, now: datetime) -> None: ...


class UserScope(Protocol):
    """Данные одного пользователя. Чужой бизнес для него не существует."""

    async def create_business(self, name: str) -> Business: ...
    async def businesses(self) -> Sequence[Business]: ...
    async def business(self, business_id: UUID) -> Business | None: ...
    async def accounts(self, business_id: UUID) -> Sequence[AdAccount]: ...
    async def available_accounts(self, business_id: UUID) -> Sequence[tuple[UUID, AdAccount]]: ...
    async def delete_business(self, business_id: UUID) -> bool: ...


class AccountTokens(Protocol):
    """Токены кабинетов одного бизнеса."""

    async def save_connected(
        self,
        login: str,
        platform: str,
        encrypted_access: str,
        encrypted_refresh: str,
        tokens: OAuthTokens,
    ) -> AdAccount: ...
    async def load(self, login: str) -> StoredTokens | None: ...
    async def replace(
        self,
        stored: StoredTokens,
        encrypted_access: str,
        encrypted_refresh: str,
        expires_at: datetime,
    ) -> bool: ...
    async def mark_needs_reconnect(self, login: str) -> None: ...
    async def save_balance(self, login: str, balance_micros: int, checked_at: datetime) -> None: ...
