"""Вход, подключение кабинета и доступ между пользователями через настоящий HTTP-слой."""

import base64
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.accounts.adapters.crypto import AesGcmCipher
from adhelper.accounts.adapters.sql import SqlAccountTokens
from adhelper.accounts.application.service import ProviderUnavailable, ReconnectRequired, TokenVault
from adhelper.accounts.domain.model import ExternalIdentity
from adhelper.api.app import create_app
from adhelper.config import Settings
from tests.accounts.fake_provider import FakeProvider

BASE = "https://adhelper.test"
NOW = datetime.now(UTC)
KEYS = "k1:" + base64.b64encode(b"k" * 32).decode()
ANNA = ExternalIdentity("uid-anna", "anna-direct", "anna@example.test")
BORIS = ExternalIdentity("uid-boris", "boris-direct", "boris@example.test")

Sessions = async_sessionmaker[AsyncSession]


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
        }
    )


Browser = Callable[[], httpx.AsyncClient]


@pytest.fixture
async def browser(api_settings: Settings, provider: FakeProvider) -> AsyncIterator[Browser]:
    app = create_app(api_settings, provider)
    clients: list[httpx.AsyncClient] = []

    def open_browser() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
        clients.append(client)
        return client

    yield open_browser
    for client in clients:
        await client.aclose()
    await app.state.engine.dispose()


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
    assert done.headers["location"] == "/"
    me = await client.get("/api/me")
    assert me.status_code == 200
    csrf: str = me.json()["csrf_token"]
    return csrf


async def _business(client: httpx.AsyncClient, csrf: str, name: str = "Салон") -> str:
    response = await client.post(
        "/api/businesses", json={"name": name}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201
    business_id: str = response.json()["id"]
    return business_id


async def _connect(
    client: httpx.AsyncClient,
    provider: FakeProvider,
    csrf: str,
    business_id: str,
    who: ExternalIdentity,
) -> httpx.Response:
    start = await client.post(
        f"/api/businesses/{business_id}/accounts/connect", headers={"X-CSRF-Token": csrf}
    )
    assert start.status_code == 200
    query = _query(start.json()["authorize_url"])
    code = provider.approve(query["code_challenge"], query["redirect_uri"], who)
    return await client.get(
        "/api/accounts/yandex/callback", params={"state": query["state"], "code": code}
    )


async def test_login_connect_and_list_account(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    csrf = await _login(client, provider, ANNA)
    business_id = await _business(client, csrf)

    done = await _connect(client, provider, csrf, business_id, ANNA)

    assert done.headers["location"] == "/?connect=ok"
    listed = (await client.get(f"/api/businesses/{business_id}/accounts")).json()
    assert [(a["login"], a["state"]) for a in listed] == [("anna-direct", "connected")]


async def test_session_cookie_is_protected(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    start = await client.get("/api/auth/yandex/login")
    query = _query(start.headers["location"])
    code = provider.approve(query["code_challenge"], query["redirect_uri"], ANNA)
    done = await client.get(
        "/api/auth/yandex/callback", params={"state": query["state"], "code": code}
    )
    cookie = next(
        value for value in done.headers.get_list("set-cookie") if value.startswith("ah_session=")
    ).lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie


async def test_pkce_challenge_is_sent(browser: Browser) -> None:
    start = await browser().get("/api/auth/yandex/login")
    query = _query(start.headers["location"])
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) >= 43


async def test_state_cannot_be_reused(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    start = await client.get("/api/auth/yandex/login")
    query = _query(start.headers["location"])
    code = provider.approve(query["code_challenge"], query["redirect_uri"], ANNA)
    params = {"state": query["state"], "code": code}
    await client.get("/api/auth/yandex/callback", params=params)
    second = browser()
    second.cookies.set("ah_oauth_state", query["state"], domain="adhelper.test", path="/api")
    # Код новый и действующий: отказать должна именно проверка state.
    fresh = provider.approve(query["code_challenge"], query["redirect_uri"], BORIS)

    replay = await second.get(
        "/api/auth/yandex/callback", params={"state": query["state"], "code": fresh}
    )

    assert replay.headers["location"] == "/?login=failed"
    assert (await second.get("/api/me")).status_code == 401


async def test_callback_in_other_browser_is_rejected(
    browser: Browser, provider: FakeProvider
) -> None:
    """Вход по ссылке злоумышленника не должен привязать жертву к его аккаунту."""
    attacker, victim = browser(), browser()
    start = await attacker.get("/api/auth/yandex/login")
    query = _query(start.headers["location"])
    code = provider.approve(query["code_challenge"], query["redirect_uri"], BORIS)

    done = await victim.get(
        "/api/auth/yandex/callback", params={"state": query["state"], "code": code}
    )

    assert done.headers["location"] == "/?login=failed"
    assert (await victim.get("/api/me")).status_code == 401


async def test_denied_login_creates_nothing(browser: Browser, engine: AsyncEngine) -> None:
    client = browser()
    start = await client.get("/api/auth/yandex/login")
    state = _query(start.headers["location"])["state"]

    done = await client.get(
        "/api/auth/yandex/callback", params={"state": state, "error": "access_denied"}
    )

    assert done.headers["location"] == "/?login=denied"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM users")) == 0


async def test_provider_outage_is_reported(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    start = await client.get("/api/auth/yandex/login")
    query = _query(start.headers["location"])
    code = provider.approve(query["code_challenge"], query["redirect_uri"], ANNA)
    provider.unavailable = True

    done = await client.get(
        "/api/auth/yandex/callback", params={"state": query["state"], "code": code}
    )

    assert done.headers["location"] == "/?login=unavailable"


async def test_write_without_csrf_is_rejected(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    csrf = await _login(client, provider, ANNA)

    missing = await client.post("/api/businesses", json={"name": "Салон"})
    wrong = await client.post(
        "/api/businesses", json={"name": "Салон"}, headers={"X-CSRF-Token": csrf + "x"}
    )

    assert (missing.status_code, wrong.status_code) == (403, 403)
    assert (await client.get("/api/businesses")).json() == []


async def test_logout_revokes_session_on_server(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    csrf = await _login(client, provider, ANNA)
    stolen = client.cookies["ah_session"]

    assert (
        await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    ).status_code == 204

    thief = browser()
    thief.cookies.set("ah_session", stolen, domain="adhelper.test", path="/")
    assert (await thief.get("/api/me")).status_code == 401


async def test_expired_session_is_rejected(
    browser: Browser, provider: FakeProvider, engine: AsyncEngine
) -> None:
    client = browser()
    await _login(client, provider, ANNA)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE sessions SET expires_at = :past"), {"past": NOW - timedelta(minutes=1)}
        )
    assert (await client.get("/api/me")).status_code == 401


async def test_foreign_business_looks_nonexistent(browser: Browser, provider: FakeProvider) -> None:
    anna, boris = browser(), browser()
    anna_csrf = await _login(anna, provider, ANNA)
    boris_csrf = await _login(boris, provider, BORIS)
    business_id = await _business(anna, anna_csrf)
    await _connect(anna, provider, anna_csrf, business_id, ANNA)

    foreign = await boris.get(f"/api/businesses/{business_id}/accounts")
    missing = await boris.get(f"/api/businesses/{uuid.uuid4()}/accounts")
    connect = await boris.post(
        f"/api/businesses/{business_id}/accounts/connect", headers={"X-CSRF-Token": boris_csrf}
    )

    assert foreign.status_code == missing.status_code == connect.status_code == 404
    assert foreign.json() == missing.json()
    assert (await boris.get("/api/businesses")).json() == []


async def test_state_of_other_user_cannot_attach_account(
    browser: Browser, provider: FakeProvider
) -> None:
    """Борис подсовывает Анне свой незавершённый поток, чтобы кабинет ушёл в его бизнес."""
    anna, boris = browser(), browser()
    await _login(anna, provider, ANNA)
    boris_csrf = await _login(boris, provider, BORIS)
    boris_business = await _business(boris, boris_csrf)
    start = await boris.post(
        f"/api/businesses/{boris_business}/accounts/connect", headers={"X-CSRF-Token": boris_csrf}
    )
    query = _query(start.json()["authorize_url"])
    code = provider.approve(query["code_challenge"], query["redirect_uri"], ANNA)
    anna.cookies.set("ah_oauth_state", query["state"], domain="adhelper.test", path="/api")

    done = await anna.get(
        "/api/accounts/yandex/callback", params={"state": query["state"], "code": code}
    )

    assert done.headers["location"] == "/?connect=failed"
    assert (await boris.get(f"/api/businesses/{boris_business}/accounts")).json() == []


async def test_same_cabinet_cannot_serve_two_businesses(
    browser: Browser, provider: FakeProvider
) -> None:
    """Два стража на один кабинет не видели бы лимиты друг друга."""
    anna, boris = browser(), browser()
    anna_csrf = await _login(anna, provider, ANNA)
    boris_csrf = await _login(boris, provider, BORIS)
    anna_business = await _business(anna, anna_csrf)
    boris_business = await _business(boris, boris_csrf)
    await _connect(anna, provider, anna_csrf, anna_business, ANNA)

    done = await _connect(boris, provider, boris_csrf, boris_business, ANNA)

    assert done.headers["location"] == "/?connect=taken"
    assert (await boris.get(f"/api/businesses/{boris_business}/accounts")).json() == []


async def test_reconnect_replaces_tokens_of_same_account(
    browser: Browser, provider: FakeProvider, engine: AsyncEngine
) -> None:
    client = browser()
    csrf = await _login(client, provider, ANNA)
    business_id = await _business(client, csrf)
    await _connect(client, provider, csrf, business_id, ANNA)
    await _connect(client, provider, csrf, business_id, ANNA)

    async with engine.connect() as connection:
        rows = (await connection.execute(text("SELECT token_version FROM ad_accounts"))).all()
    assert [row[0] for row in rows] == [2]


async def test_tokens_never_appear_in_database_responses_or_logs(
    browser: Browser, provider: FakeProvider, engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = browser()
    csrf = await _login(client, provider, ANNA)
    business_id = await _business(client, csrf)
    await _connect(client, provider, csrf, business_id, ANNA)
    listed = (await client.get(f"/api/businesses/{business_id}/accounts")).text

    async with engine.connect() as connection:
        dump = ""
        for table in ("ad_accounts", "sessions", "oauth_flows"):
            dump += str((await connection.execute(text(f"SELECT * FROM {table}"))).all())
    assert provider.issued
    for secret in provider.issued:
        assert secret not in dump
        assert secret not in listed
        assert secret not in caplog.text
    assert client.cookies["ah_session"] not in dump


def _vault(sessions: Sessions, business: uuid.UUID, provider: FakeProvider, now: datetime) -> Any:
    return TokenVault(
        SqlAccountTokens(sessions, business),
        business,
        provider,
        AesGcmCipher.from_config(KEYS),
        lambda: now,
    )


@dataclass
class Setup:
    business: uuid.UUID
    expiry: datetime


@pytest.fixture
async def connected(browser: Browser, provider: FakeProvider) -> Setup:
    client = browser()
    csrf = await _login(client, provider, ANNA)
    business_id = await _business(client, csrf)
    await _connect(client, provider, csrf, business_id, ANNA)
    return Setup(uuid.UUID(business_id), provider.now + timedelta(days=30))


async def test_vault_returns_token_while_valid(
    sessions: Sessions, provider: FakeProvider, connected: Setup
) -> None:
    token = await _vault(sessions, connected.business, provider, NOW).access_token("anna-direct")
    assert token in provider.issued
    assert provider.refresh_calls == 0


async def test_vault_refreshes_before_expiry(
    sessions: Sessions, provider: FakeProvider, connected: Setup
) -> None:
    near_expiry = connected.expiry - timedelta(minutes=1)
    provider.now = near_expiry
    vault = _vault(sessions, connected.business, provider, near_expiry)

    first = await vault.access_token("anna-direct")

    assert provider.refresh_calls == 1
    assert first == provider.issued[-2]
    later = _vault(sessions, connected.business, provider, near_expiry)
    assert await later.access_token("anna-direct") == first
    assert provider.refresh_calls == 1


async def test_rejected_refresh_marks_account_for_reconnect(
    sessions: Sessions, provider: FakeProvider, connected: Setup, engine: AsyncEngine
) -> None:
    provider.refresh_tokens.clear()
    vault = _vault(sessions, connected.business, provider, connected.expiry)

    with pytest.raises(ReconnectRequired):
        await vault.access_token("anna-direct")

    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM ad_accounts")) == "needs_reconnect"


async def test_provider_outage_keeps_tokens_for_retry(
    sessions: Sessions, provider: FakeProvider, connected: Setup, engine: AsyncEngine
) -> None:
    provider.unavailable = True
    vault = _vault(sessions, connected.business, provider, connected.expiry)

    with pytest.raises(ProviderUnavailable):
        await vault.access_token("anna-direct")

    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT state FROM ad_accounts")) == "connected"
        assert await connection.scalar(text("SELECT token_version FROM ad_accounts")) == 1


async def test_vault_of_other_business_cannot_read_token(
    sessions: Sessions, provider: FakeProvider, connected: Setup
) -> None:
    with pytest.raises(ReconnectRequired):
        await _vault(sessions, uuid.uuid4(), provider, NOW).access_token("anna-direct")


async def test_login_is_closed_without_encryption_keys(
    migrated: Settings, provider: FakeProvider
) -> None:
    app = create_app(migrated.model_copy(update={"token_encryption_keys": None}), provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        assert (await client.get("/api/auth/yandex/login")).status_code == 503
    await app.state.engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO budget_periods (business_id, account, starts_at, ends_at, hard_limit_micros,"
        " reserve_bp, rules_version, next_check_at)"
        " VALUES (gen_random_uuid(), 'нет', now(), now(), 1, 0, 'budget-1', now())",
        "INSERT INTO alerts (business_id, kind, details)"
        " VALUES (gen_random_uuid(), 'x', '{}'::jsonb)",
    ],
)
async def test_rows_of_unknown_business_are_rejected(engine: AsyncEngine, statement: str) -> None:
    async with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            await connection.execute(text(statement))


async def test_journal_constraint_exists(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        found = await connection.scalar(
            text("SELECT count(*) FROM pg_constraint WHERE conname = 'fk_changes_business_account'")
        )
    assert found == 1
