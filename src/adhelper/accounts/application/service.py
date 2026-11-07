import base64
import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from adhelper.accounts.domain.model import (
    AccountState,
    AdAccount,
    Business,
    FlowPurpose,
    OAuthFlow,
    OAuthTokens,
    SessionInfo,
    StoredTokens,
)
from adhelper.accounts.domain.ports import (
    AccountTaken,
    AccountTokens,
    DecryptionFailed,
    GrantRejected,
    IdentityStore,
    OAuthProvider,
    ProviderUnavailable,
    TokenCipher,
    UserScope,
)

__all__ = [
    "AccountState",
    "AccountTaken",
    "AccountsService",
    "BusinessNotFound",
    "FlowInvalid",
    "GrantRejected",
    "LoginUnknown",
    "NewSession",
    "OAuthProvider",
    "ProviderUnavailable",
    "ReconnectRequired",
    "TokenInvalid",
    "TokenVault",
    "hash_token",
]

logger = logging.getLogger(__name__)

FLOW_TTL = timedelta(minutes=10)
SESSION_TTL = timedelta(days=30)
REFRESH_MARGIN = timedelta(minutes=5)
LOGIN_SCOPES = ("login:info", "login:email")
CONNECT_SCOPES = ("direct:api", "login:info")
DIRECT_PLATFORM = "yandex_direct"
# Токен, выданный вручную, живёт около года и не обновляется: срок берём с запасом.
MANUAL_TOKEN_LIFETIME = timedelta(days=330)
MANUAL_SCOPES = ("direct:api",)
VERIFIER_CONTEXT = "oauth_verifier"


class FlowInvalid(Exception):
    """state неизвестен, устарел, уже использован или выдан другому браузеру либо пользователю."""


class LoginUnknown(Exception):
    """По токену не удалось узнать логин: пусть пользователь впишет его сам."""


class BusinessNotFound(Exception):
    """Чужой бизнес и несуществующий неразличимы намеренно."""


class TokenInvalid(Exception):
    """Токен отклонён площадкой или недействителен."""


class ReconnectRequired(Exception):
    pass


@dataclass(frozen=True)
class NewSession:
    token: str
    csrf_token: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def token_context(business_id: UUID, login: str, kind: str) -> str:
    return f"ad_account:{business_id}:{login}:{kind}"


class TokenVault:
    """Единственное место, где токен кабинета расшифровывается."""

    def __init__(
        self,
        tokens: AccountTokens,
        business_id: UUID,
        provider: OAuthProvider,
        cipher: TokenCipher,
        clock: Callable[[], datetime],
    ) -> None:
        self._tokens = tokens
        self._business_id = business_id
        self._provider = provider
        self._cipher = cipher
        self._clock = clock

    def _access(self, stored: StoredTokens) -> str:
        context = token_context(self._business_id, stored.login, "access")
        return self._cipher.decrypt(stored.encrypted_access, context)

    async def access_token(self, login: str) -> str:
        stored = await self._tokens.load(login)
        if stored is None:
            raise ReconnectRequired
        try:
            if stored.expires_at - self._clock() > REFRESH_MARGIN:
                return self._access(stored)
            refresh_context = token_context(self._business_id, login, "refresh")
            refresh = self._cipher.decrypt(stored.encrypted_refresh, refresh_context)
            if not refresh:
                # Токен выдан вручную: обновить нечем, нужен новый.
                await self._tokens.mark_needs_reconnect(login)
                raise ReconnectRequired
            try:
                fresh = await self._provider.refresh(refresh)
            except GrantRejected:
                await self._tokens.mark_needs_reconnect(login)
                raise ReconnectRequired from None
            saved = await self._tokens.replace(
                stored,
                self._cipher.encrypt(
                    fresh.access, token_context(self._business_id, login, "access")
                ),
                self._cipher.encrypt(fresh.refresh, refresh_context),
                fresh.expires_at,
            )
            if saved:
                return fresh.access
            # Другой процесс успел раньше: его токены действующие, берём их.
            current = await self._tokens.load(login)
            if current is None:
                raise ReconnectRequired
            return self._access(current)
        except DecryptionFailed:
            await self._tokens.mark_needs_reconnect(login)
            raise ReconnectRequired from None


class AccountsService:
    def __init__(
        self,
        identity_store: IdentityStore,
        scope_for: Callable[[UUID], UserScope],
        tokens_for: Callable[[UUID], AccountTokens],
        provider: OAuthProvider,
        cipher: TokenCipher,
        clock: Callable[[], datetime],
        public_base_url: str,
        resolve_login: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        self._resolve_login = resolve_login
        self._store = identity_store
        self._scope_for = scope_for
        self._tokens_for = tokens_for
        self._provider = provider
        self._cipher = cipher
        self._clock = clock
        self._base = public_base_url.rstrip("/")

    def _login_redirect(self) -> str:
        return f"{self._base}/api/auth/yandex/callback"

    def _connect_redirect(self) -> str:
        return f"{self._base}/api/accounts/yandex/callback"

    async def _begin(
        self,
        purpose: FlowPurpose,
        redirect_uri: str,
        scopes: Sequence[str],
        user_id: UUID | None = None,
        business_id: UUID | None = None,
    ) -> tuple[str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        # Адрес раньше записи: без провайдера отказ не оставляет брошенных попыток входа.
        url = self._provider.authorize_url(state, _challenge(verifier), redirect_uri, scopes)
        await self._store.save_flow(
            OAuthFlow(
                hash_token(state),
                purpose,
                self._cipher.encrypt(verifier, VERIFIER_CONTEXT),
                self._clock() + FLOW_TTL,
                user_id,
                business_id,
            )
        )
        return url, state

    async def _consume(self, state: str, purpose: FlowPurpose) -> OAuthFlow:
        flow = await self._store.consume_flow(hash_token(state), self._clock())
        if flow is None or flow.purpose is not purpose:
            raise FlowInvalid
        return flow

    async def begin_login(self) -> tuple[str, str]:
        return await self._begin(FlowPurpose.LOGIN, self._login_redirect(), LOGIN_SCOPES)

    async def cancel_login(self, state: str) -> None:
        await self._consume(state, FlowPurpose.LOGIN)

    async def cancel_connect(self, state: str) -> None:
        await self._consume(state, FlowPurpose.CONNECT)

    async def complete_login(self, state: str, code: str) -> NewSession:
        flow = await self._consume(state, FlowPurpose.LOGIN)
        verifier = self._cipher.decrypt(flow.encrypted_verifier, VERIFIER_CONTEXT)
        tokens = await self._provider.exchange(code, verifier, self._login_redirect())
        user = await self._store.upsert_user(await self._provider.identity(tokens.access))
        session = NewSession(secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        await self._store.create_session(
            user.id, hash_token(session.token), session.csrf_token, self._clock() + SESSION_TTL
        )
        return session

    async def session(self, token: str) -> SessionInfo | None:
        return await self._store.find_session(hash_token(token), self._clock())

    async def logout(self, token: str) -> None:
        await self._store.revoke_session(hash_token(token), self._clock())

    async def create_business(self, user_id: UUID, name: str) -> Business:
        return await self._scope_for(user_id).create_business(name)

    async def businesses(self, user_id: UUID) -> Sequence[Business]:
        return await self._scope_for(user_id).businesses()

    async def require_business(self, user_id: UUID, business_id: UUID) -> Business:
        """Бизнес пользователя или BusinessNotFound: чужой и несуществующий неотличимы."""
        business = await self._scope_for(user_id).business(business_id)
        if business is None:
            raise BusinessNotFound
        return business

    async def accounts(self, user_id: UUID, business_id: UUID) -> Sequence[AdAccount]:
        scope = self._scope_for(user_id)
        if await scope.business(business_id) is None:
            raise BusinessNotFound
        found = await scope.accounts(business_id)
        result: list[AdAccount] = []
        tokens = self._tokens_for(business_id)
        for account in found:
            if account.state == AccountState.CONNECTED:
                stored = await tokens.load(account.login)
                if stored is not None:
                    try:
                        self._cipher.decrypt(
                            stored.encrypted_access,
                            token_context(business_id, account.login, "access"),
                        )
                    except DecryptionFailed:
                        await tokens.mark_needs_reconnect(account.login)
                        account = AdAccount(
                            account.id,
                            account.business_id,
                            account.platform,
                            account.login,
                            account.scopes,
                            AccountState.NEEDS_RECONNECT,
                        )
            result.append(account)
        return result

    async def begin_connect(self, user_id: UUID, business_id: UUID) -> tuple[str, str]:
        if await self._scope_for(user_id).business(business_id) is None:
            raise BusinessNotFound
        return await self._begin(
            FlowPurpose.CONNECT, self._connect_redirect(), CONNECT_SCOPES, user_id, business_id
        )

    async def complete_connect(self, user_id: UUID, state: str, code: str) -> AdAccount:
        flow = await self._consume(state, FlowPurpose.CONNECT)
        # Чужой state не должен привязать чужой кабинет к этому пользователю.
        if flow.user_id != user_id or flow.business_id is None:
            raise FlowInvalid
        verifier = self._cipher.decrypt(flow.encrypted_verifier, VERIFIER_CONTEXT)
        tokens = await self._provider.exchange(code, verifier, self._connect_redirect())
        login = (await self._provider.identity(tokens.access)).login
        return await self._tokens_for(flow.business_id).save_connected(
            login,
            DIRECT_PLATFORM,
            self._cipher.encrypt(tokens.access, token_context(flow.business_id, login, "access")),
            self._cipher.encrypt(tokens.refresh, token_context(flow.business_id, login, "refresh")),
            tokens,
        )

    async def connect_manual(
        self, user_id: UUID, business_id: UUID, token: str, login: str | None
    ) -> AdAccount:
        """Кабинет по токену, который пользователь получил сам. Токен не обновляется."""
        if await self._scope_for(user_id).business(business_id) is None:
            raise BusinessNotFound
        resolved: str | None = None
        if self._resolve_login:
            resolved = await self._resolve_login(token)
        if resolved:
            login = resolved
        elif not login:
            raise LoginUnknown
        login = login.strip().lower()
        tokens = OAuthTokens(token, "", self._clock() + MANUAL_TOKEN_LIFETIME, MANUAL_SCOPES)
        account = await self._tokens_for(business_id).save_connected(
            login,
            DIRECT_PLATFORM,
            self._cipher.encrypt(token, token_context(business_id, login, "access")),
            self._cipher.encrypt("", token_context(business_id, login, "refresh")),
            tokens,
        )
        logger.info("Подключён ручной токен кабинета %s для бизнеса %s", login, business_id)
        return account
