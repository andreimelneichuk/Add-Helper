"""Яндекс ID: вход владельца и доступ к кабинету Директа (docs/03-integrations.md «Вход»).

Формат по документации Яндекс ID, реальным вызовом ещё не проверен (V1, T4):
- `GET {oauth}/authorize?response_type=code&client_id&redirect_uri&scope&state&code_challenge
  &code_challenge_method=S256`;
- `POST {oauth}/token` формой: `grant_type=authorization_code`, `code`, `code_verifier`
  или `grant_type=refresh_token`, `refresh_token`; приложение — `client_id` и `client_secret`;
  ответ `access_token`, `refresh_token`, `expires_in`, ошибка 400 с `error`;
- `GET {login}/info?format=json` с заголовком `Authorization: OAuth <токен>`: `id`, `login`,
  `default_email`.

Адреса задаются конфигурацией: заказчик подключает своё приложение, стенд — подставной сервер.
Токены, код и секрет в исключения и журнал не попадают: наружу только тип ошибки и код ответа.
"""

import functools
import logging
import ssl
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from adhelper.accounts.domain.model import ExternalIdentity, OAuthTokens
from adhelper.accounts.domain.ports import GrantRejected, ProviderUnavailable

# В журнале httpx адреса запросов; секретов в адресах нет, но лишнего не пишем.
logging.getLogger("httpx").setLevel(logging.WARNING)

OAUTH = "https://oauth.yandex.ru"
LOGIN = "https://login.yandex.ru"
TIMEOUT_SECONDS = 15
# Ошибки, после которых повтор того же кода или токена бесполезен: нужен новый вход.
REJECTED = {"invalid_grant", "invalid_client", "unauthorized_client", "bad_verification_code"}


@functools.cache
def _yandex_ssl_context() -> ssl.SSLContext:
    """SSL-контекст с TLS 1.2: шлюзы Яндекс ID / Passport зависают на TLS 1.3 ClientHello OpenSSL 3."""
    ctx = ssl.create_default_context()
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class YandexOAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        clock: Callable[[], datetime],
        oauth_url: str = OAUTH,
        login_url: str = LOGIN,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        for url in (oauth_url, login_url):
            if not url.startswith(("https://", "http://")):
                raise ValueError(f"Адрес Яндекс ID должен начинаться с https://: {url!r}")
        self._client_id = client_id
        self._secret = client_secret
        self._clock = clock
        self._oauth = oauth_url.rstrip("/")
        self._login = login_url.rstrip("/")
        self._client = client

    def authorize_url(
        self, state: str, code_challenge: str, redirect_uri: str, scopes: Sequence[str]
    ) -> str:
        query = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if scopes:
            query["scope"] = " ".join(scopes)
        return f"{self._oauth}/authorize?{urlencode(query)}"

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self._client is not None:
                return await self._client.request(method, url, **kwargs)
            async with httpx.AsyncClient(
                verify=_yandex_ssl_context(), timeout=TIMEOUT_SECONDS
            ) as client:
                return await client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise ProviderUnavailable(type(error).__name__) from None

    async def _token(self, form: dict[str, str]) -> OAuthTokens:
        form = {
            **form,
            "client_id": self._client_id,
            "client_secret": self._secret.get_secret_value(),
        }
        response = await self._send("POST", f"{self._oauth}/token", data=form)
        body = _json(response)
        if response.status_code >= 500:
            raise ProviderUnavailable(f"token: {response.status_code}")
        if response.status_code != 200:
            error = body.get("error") if isinstance(body, dict) else None
            if error in REJECTED or response.status_code in (400, 401):
                raise GrantRejected(str(error or response.status_code))
            raise ProviderUnavailable(f"token: {response.status_code}")
        try:
            access = str(body["access_token"])
            refresh = str(body["refresh_token"])
            expires_in = int(body["expires_in"])
        except (KeyError, TypeError, ValueError):
            raise ProviderUnavailable("token: unexpected answer") from None
        scope = body.get("scope")
        scopes = tuple(scope.split()) if isinstance(scope, str) else ()
        return OAuthTokens(access, refresh, self._clock() + timedelta(seconds=expires_in), scopes)

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> OAuthTokens:
        # redirect_uri Яндекс сверяет с приложением при выдаче кода; в обмене его не ждёт.
        return await self._token(
            {"grant_type": "authorization_code", "code": code, "code_verifier": verifier}
        )

    async def refresh(self, refresh_token: str) -> OAuthTokens:
        return await self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def identity(self, access_token: str) -> ExternalIdentity:
        response = await self._send(
            "GET",
            f"{self._login}/info",
            params={"format": "json"},
            headers={"Authorization": f"OAuth {access_token}"},
        )
        if response.status_code == 401:
            raise GrantRejected("info: 401")
        if response.status_code != 200:
            raise ProviderUnavailable(f"info: {response.status_code}")
        body = _json(response)
        try:
            uid, login = str(body["id"]), str(body["login"])
        except (KeyError, TypeError):
            raise ProviderUnavailable("info: unexpected answer") from None
        email = body.get("default_email")
        return ExternalIdentity(uid, login, email if isinstance(email, str) else None)


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}
