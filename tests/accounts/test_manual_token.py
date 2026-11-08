"""Тесты ручного подключения токена Яндекс Директа (Задача 18).

Проверяют:
1. Валидацию токена через мок DirectHttpClient (Clients.get).
2. Отказ при передаче невалидного или пустого токена.
3. Шифрование и запись в базу данных.
4. Мягкую обработку сбоев дешифрования без 500 ошибки.
"""

import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.accounts.domain.model import ExternalIdentity
from adhelper.api.app import create_app
from adhelper.config import Settings
from adhelper.platforms.adapters.direct.factory import DirectPlatforms
from adhelper.platforms.adapters.direct.transport import DirectAuthError, DirectHttpClient
from tests.accounts.fake_provider import FakeProvider

BASE = "https://adhelper.test"
NOW = datetime.now(UTC)
KEYS = "k1:" + base64.b64encode(b"k" * 32).decode()
ANNA = ExternalIdentity("uid-anna", "anna-direct", "anna@example.test")

Sessions = async_sessionmaker[AsyncSession]
Browser = Callable[[], httpx.AsyncClient]


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(NOW)


@pytest.fixture
def api_settings(migrated: Settings) -> Settings:
    return migrated.model_copy(
        update={
            "token_encryption_keys": SecretStr(KEYS),
            "public_base_url": BASE,
            "cookie_secure": True,
            "yandex_manual_client_id": "manual-app-id",
        }
    )


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


async def _login(client: httpx.AsyncClient, provider: FakeProvider, who: ExternalIdentity) -> str:
    start = await client.get("/api/auth/yandex/login")
    assert start.status_code == 302
    query = _query(start.headers["location"])
    code = provider.approve(query["code_challenge"], query["redirect_uri"], who)
    done = await client.get(
        "/api/auth/yandex/callback", params={"state": query["state"], "code": code}
    )
    assert done.status_code == 302
    me = await client.get("/api/me")
    assert me.status_code == 200
    return str(me.json()["csrf_token"])


async def _business(client: httpx.AsyncClient, csrf: str) -> str:
    response = await client.post(
        "/api/businesses", json={"name": "Тестовый салон"}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201
    return str(response.json()["id"])


class _MockDirectClient(DirectHttpClient):
    """Мок HTTP-клиента Директа для проверки вызова Clients.get."""

    def __init__(self, owner: str | None = None, should_fail_auth: bool = False) -> None:
        self.owner = owner
        self.should_fail_auth = should_fail_auth
        self.checked_tokens: list[str] = []

    async def token_owner(self, token: str) -> str | None:
        self.checked_tokens.append(token)
        if self.should_fail_auth:
            raise DirectAuthError(53, "Authorization error: token is invalid or expired")
        return self.owner

    async def aclose(self) -> None:
        pass


async def test_manual_token_validates_via_direct_client_and_stores_encrypted(
    api_settings: Settings, provider: FakeProvider, engine: AsyncEngine
) -> None:
    """Валидация токена через DirectHttpClient, получение логина и шифрование в базе."""
    app = create_app(api_settings, provider)
    mock_client = _MockDirectClient(owner="verified-salon-owner")
    app.state.platform_for = DirectPlatforms(
        mock_client, app.state.sessions, provider, app.state.cipher, lambda: NOW
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        csrf = await _login(client, provider, ANNA)
        business_id = await _business(client, csrf)

        token_input = "y0_valid_direct_token_1234567890"
        response = await client.post(
            f"/api/businesses/{business_id}/accounts/manual-token",
            json={"token": token_input},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["login"] == "verified-salon-owner"
        assert data["state"] == "connected"
        assert token_input not in response.text
        assert mock_client.checked_tokens == [token_input]

        # Проверяем, что в базе токен зашифрован и не хранится в открытом виде
        async with engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT external_login, encrypted_access, state, token_expires_at "
                            "FROM ad_accounts WHERE business_id = :bid"
                        ),
                        {"bid": UUID(business_id)},
                    )
                )
                .mappings()
                .one()
            )
            assert row["external_login"] == "verified-salon-owner"
            assert row["state"] == "connected"
            assert token_input not in row["encrypted_access"]
            # Срок действия ~330 дней
            assert row["token_expires_at"] > NOW + timedelta(days=300)

    await app.state.engine.dispose()


async def test_manual_token_rejected_when_direct_auth_fails(
    api_settings: Settings, provider: FakeProvider
) -> None:
    """Отказ при передаче невалидного токена, отклонённого Директом (400 Bad Request)."""
    app = create_app(api_settings, provider)
    mock_client = _MockDirectClient(should_fail_auth=True)
    app.state.platform_for = DirectPlatforms(
        mock_client, app.state.sessions, provider, app.state.cipher, lambda: NOW
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        csrf = await _login(client, provider, ANNA)
        business_id = await _business(client, csrf)

        response = await client.post(
            f"/api/businesses/{business_id}/accounts/manual-token",
            json={"token": "y0_rejected_token_1234567890"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400
        assert "токен не подошёл" in response.json()["detail"].lower()
        assert "y0_rejected_token_1234567890" not in response.text

    await app.state.engine.dispose()


@pytest.mark.parametrize("empty_token", ["", "   ", "short", "token with spaces 1234567890"])
async def test_manual_token_rejected_on_empty_or_malformed(
    api_settings: Settings, provider: FakeProvider, empty_token: str
) -> None:
    """Отказ при передаче пустого или несоответствующего формату токена (422)."""
    app = create_app(api_settings, provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        csrf = await _login(client, provider, ANNA)
        business_id = await _business(client, csrf)

        response = await client.post(
            f"/api/businesses/{business_id}/accounts/manual-token",
            json={"token": empty_token},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422

    await app.state.engine.dispose()


async def test_accounts_soft_handles_unreadable_token_without_500(
    api_settings: Settings, provider: FakeProvider, engine: AsyncEngine
) -> None:
    """При нечитаемом ключе/шифртексте эндпоинт accounts не падает с 500,
    а отдаёт needs_reconnect.
    """
    app = create_app(api_settings, provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        csrf = await _login(client, provider, ANNA)
        business_id = await _business(client, csrf)

        # Создаём валидный аккаунт
        res = await client.post(
            f"/api/businesses/{business_id}/accounts/manual-token",
            json={"login": "test-owner", "token": "y0_initial_token_1234567890"},
            headers={"X-CSRF-Token": csrf},
        )
        assert res.status_code == 200

        # Портим шифртекст в базе (симуляция старого ключа)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE ad_accounts SET encrypted_access = 'old_key.corrupted_ciphertext_data' "
                    "WHERE business_id = :bid"
                ),
                {"bid": UUID(business_id)},
            )

        # Запрос списка аккаунтов не должен выдавать 500
        listed = await client.get(f"/api/businesses/{business_id}/accounts")
        assert listed.status_code == 200
        accounts = listed.json()
        assert len(accounts) == 1
        account = accounts[0]
        assert account["state"] == "needs_reconnect"
        assert account.get("status") == "needs_reconnect"
        assert account.get("reason") == "token_unreadable"

    await app.state.engine.dispose()
