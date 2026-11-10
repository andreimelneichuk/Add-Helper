"""Сборка площадки Директа для воркера и API: `platform_for(business_id, login)`.

Токен кабинета берётся из хранилища своего бизнеса и расшифровывается только в `TokenVault`,
поэтому кабинет чужого бизнеса не получит токен даже по известному логину.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.accounts.adapters.crypto import DecryptionFailed
from adhelper.accounts.adapters.sql import SqlAccountTokens
from adhelper.accounts.adapters.yandex_oauth import SplitYandexOAuth, YandexOAuth
from adhelper.accounts.application.service import (
    OAuthProvider,
    ReconnectRequired,
    TokenInvalid,
    TokenVault,
)
from adhelper.accounts.domain.ports import TokenCipher
from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.business.adapters.tag_checks import SqlTagChecks
from adhelper.config import Settings
from adhelper.ledger.adapters.sql import SqlChangeRepository
from adhelper.platforms.adapters.direct.geo_cache_sql import SqlGeoRegionCache
from adhelper.platforms.adapters.direct.platform import DirectPlatform
from adhelper.platforms.adapters.direct.transport import (
    DirectAuthError,
    DirectError,
    DirectHttpClient,
    TokenSource,
)
from adhelper.platforms.adapters.metrika import MetrikaClient


def yandex_provider(settings: Settings, clock: Callable[[], datetime]) -> OAuthProvider | None:
    secret = settings.yandex_client_secret
    if not settings.yandex_client_id or secret is None or not secret.get_secret_value():
        return None
    login = YandexOAuth(
        settings.yandex_client_id,
        secret,
        clock,
        settings.yandex_oauth_url,
        settings.yandex_login_url,
    )
    direct_secret = settings.yandex_direct_client_secret
    if not settings.yandex_direct_client_id or direct_secret is None:
        return login
    if not direct_secret.get_secret_value():
        return login
    # Второе приложение: у него одобрен доступ к API Директа, вход остаётся в первом.
    direct = YandexOAuth(
        settings.yandex_direct_client_id,
        direct_secret,
        clock,
        settings.yandex_oauth_url,
        settings.yandex_login_url,
    )
    return SplitYandexOAuth(login, direct)


class DirectPlatforms:
    """Один HTTP-клиент на процесс, площадка на каждый бизнес."""

    def __init__(
        self,
        client: DirectHttpClient,
        sessions: async_sessionmaker[AsyncSession],
        provider: OAuthProvider,
        cipher: TokenCipher,
        clock: Callable[[], datetime],
        metrika_url: str = "https://api-metrika.yandex.net",
    ) -> None:
        self._metrika_url = metrika_url
        self._client = client
        self._sessions = sessions
        self._provider = provider
        self._cipher = cipher
        self._clock = clock
        self._geo_region_cache = SqlGeoRegionCache(sessions)

    def _tokens(self, business_id: UUID) -> TokenSource:
        vault = TokenVault(
            SqlAccountTokens(self._sessions, business_id, self._cipher),
            business_id,
            self._provider,
            self._cipher,
            self._clock,
        )

        async def token_for(login: str) -> str:
            try:
                return await vault.access_token(login)
            except ReconnectRequired as error:
                raise DirectAuthError(None, "account needs reconnect") from error
            except DecryptionFailed as error:
                # Токен записан другим ключом: для владельца это то же, что просьба переподключить.
                raise DirectAuthError(None, "stored token is unreadable") from error

        return token_for

    async def owner_login(self, token: str) -> str | None:
        """Логин владельца токена или None, если Директ не ответил."""
        try:
            return await self._client.token_owner(token)
        except DirectAuthError as error:
            raise TokenInvalid("Токен отклонён Яндекс Директом") from error
        except DirectError:
            return None

    def api_points(self, account: str) -> int | None:
        """Сколько баллов API осталось у логина по последнему ответу Директа."""
        return self._client.points_rest(account)

    def metrika(self, business_id: UUID, login: str) -> MetrikaClient:
        """Метрика кабинета: токен тот же, что у Директа."""
        token_for = self._tokens(business_id)

        async def token() -> str:
            try:
                return await token_for(login)
            except DirectAuthError as error:
                raise PlatformUnavailable("metrika token unavailable") from error

        return MetrikaClient(token, self._metrika_url)

    def __call__(self, business_id: UUID, login: str) -> DirectPlatform:
        async def campaign_ids_for(account: str) -> tuple[str, ...]:
            return await SqlChangeRepository.campaign_ids_for(self._sessions, business_id, account)

        async def campaign_targets_for(account: str) -> tuple[str, ...]:
            return await SqlChangeRepository.campaign_targets_for(
                self._sessions, business_id, account
            )

        counters = SqlTagChecks(self._sessions, business_id)

        async def goals_for(account: str) -> list[int]:
            # Нет счётчика — целей не знаем, отчёт идёт по всем целям, как раньше.
            counter = await counters.counter()
            if counter is None:
                return []
            return await self.metrika(business_id, account).lead_goals(counter)

        return DirectPlatform(
            self._client,
            self._tokens(business_id),
            self._clock,
            campaign_ids_for,
            campaign_targets_for,
            self._geo_region_cache,
            goals_for,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_direct_platforms(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    provider: OAuthProvider | None,
    cipher: TokenCipher | None,
    clock: Callable[[], datetime],
) -> DirectPlatforms | None:
    """Без флага, ключей шифрования или приложения Яндекс ID площадки нет: тревога и 503."""
    if not settings.direct_enabled or provider is None or cipher is None:
        return None
    client = DirectHttpClient(settings.direct_api_url, settings.direct_live_url)
    return DirectPlatforms(client, sessions, provider, cipher, clock, settings.metrika_api_url)
