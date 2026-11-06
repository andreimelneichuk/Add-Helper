"""Клиент Яндекс ID на подставном транспорте: формат запросов и ответов по документации.

Реального вызова ещё не было (V1): после него ответы заменим записанными, без токенов.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from adhelper.accounts.adapters.yandex_oauth import YandexOAuth
from adhelper.accounts.domain.ports import GrantRejected, ProviderUnavailable

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
SECRET = "app-secret-value"


def _oauth(handler: httpx.MockTransport) -> YandexOAuth:
    client = httpx.AsyncClient(transport=handler)
    return YandexOAuth("app-id", SecretStr(SECRET), lambda: NOW, client=client)


def test_authorize_url_carries_pkce_state_and_scopes() -> None:
    url = _oauth(httpx.MockTransport(lambda _r: httpx.Response(500))).authorize_url(
        "st", "chal", "https://adhelper.test/api/auth/yandex/callback", ["direct:api"]
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://oauth.yandex.ru/authorize"
    assert query == {
        "response_type": ["code"],
        "client_id": ["app-id"],
        "redirect_uri": ["https://adhelper.test/api/auth/yandex/callback"],
        "state": ["st"],
        "code_challenge": ["chal"],
        "code_challenge_method": ["S256"],
        "scope": ["direct:api"],
    }
    assert SECRET not in url


async def test_exchange_sends_verifier_and_reads_tokens() -> None:
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://oauth.yandex.ru/token"
        seen.append(parse_qs(request.content.decode()))
        body = {
            "access_token": "A",
            "refresh_token": "R",
            "expires_in": 3600,
            "token_type": "bearer",
        }
        return httpx.Response(200, json=body)

    tokens = await _oauth(httpx.MockTransport(handler)).exchange("code1", "ver1", "unused")
    assert (tokens.access, tokens.refresh) == ("A", "R")
    assert tokens.expires_at == NOW + timedelta(hours=1)
    assert seen[0]["grant_type"] == ["authorization_code"]
    assert seen[0]["code"] == ["code1"] and seen[0]["code_verifier"] == ["ver1"]
    assert seen[0]["client_secret"] == [SECRET]


async def test_refresh_uses_refresh_grant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["R"]
        return httpx.Response(
            200, json={"access_token": "A2", "refresh_token": "R2", "expires_in": 60}
        )

    tokens = await _oauth(httpx.MockTransport(handler)).refresh("R")
    assert tokens.access == "A2"


async def test_bad_code_is_rejected_and_secret_stays_out_of_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Code has expired"}
        )

    with pytest.raises(GrantRejected) as error:
        await _oauth(httpx.MockTransport(handler)).exchange("old", "v", "u")
    assert SECRET not in str(error.value) and "old" not in str(error.value)


@pytest.mark.parametrize("status", [500, 503])
async def test_server_error_is_unavailable_not_rejected(status: int) -> None:
    oauth = _oauth(httpx.MockTransport(lambda _r: httpx.Response(status)))
    with pytest.raises(ProviderUnavailable):
        await oauth.exchange("c", "v", "u")


async def test_network_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderUnavailable):
        await _oauth(httpx.MockTransport(handler)).refresh("R")


async def test_identity_reads_info_with_oauth_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/info" and request.url.params["format"] == "json"
        assert request.headers["Authorization"] == "OAuth A"
        body = {"id": "1130000012345", "login": "nogti-studio", "default_email": "o@ya.ru"}
        return httpx.Response(200, json=body)

    who = await _oauth(httpx.MockTransport(handler)).identity("A")
    assert (who.uid, who.login, who.email) == ("1130000012345", "nogti-studio", "o@ya.ru")


async def test_revoked_token_on_info_is_rejected() -> None:
    oauth = _oauth(httpx.MockTransport(lambda _r: httpx.Response(401)))
    with pytest.raises(GrantRejected):
        await oauth.identity("A")


def test_addresses_come_from_configuration_and_must_be_http() -> None:
    oauth = YandexOAuth("id", SecretStr("s"), lambda: NOW, oauth_url="http://stand.test/oauth/")
    assert oauth.authorize_url("s", "c", "r", []).startswith("http://stand.test/oauth/authorize?")
    with pytest.raises(ValueError):
        YandexOAuth("id", SecretStr("s"), lambda: NOW, oauth_url="file:///etc")


def test_yandex_ssl_context_uses_tls_1_2() -> None:
    import ssl

    from adhelper.accounts.adapters.yandex_oauth import _yandex_ssl_context

    ctx = _yandex_ssl_context()
    assert ctx.maximum_version == ssl.TLSVersion.TLSv1_2
