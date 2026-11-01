from collections.abc import Sequence
from typing import NoReturn

from adhelper.accounts.domain.ports import ProviderUnavailable


class UnconfiguredProvider:
    """Провайдер входа ещё не подключён (V1). Сессии работают, новый вход отвечает отказом."""

    def authorize_url(
        self, state: str, code_challenge: str, redirect_uri: str, scopes: Sequence[str]
    ) -> NoReturn:
        raise ProviderUnavailable

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> NoReturn:
        raise ProviderUnavailable

    async def refresh(self, refresh_token: str) -> NoReturn:
        raise ProviderUnavailable

    async def identity(self, access_token: str) -> NoReturn:
        raise ProviderUnavailable
