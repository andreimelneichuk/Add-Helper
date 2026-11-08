"""Тесты удаления бизнеса: soft-delete, освобождение токена кабинета и изоляция."""

import base64
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from adhelper.api.app import create_app
from adhelper.config import Settings
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import (
    ANNA,
    BORIS,
    _business,
    _connect,
    _login,
)

BASE = "https://adhelper.test"
NOW = datetime.now(UTC)
KEYS = "k1:" + base64.b64encode(b"k" * 32).decode()

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
        }
    )


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


async def test_delete_business_success(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    csrf = await _login(client, provider, ANNA)

    b1 = await _business(client, csrf, "Салон 1")
    b2 = await _business(client, csrf, "Салон 2")

    listed = (await client.get("/api/businesses")).json()
    assert len(listed) == 2
    assert {b["id"] for b in listed} == {b1, b2}

    # Удаляем первый бизнес
    delete_res = await client.delete(f"/api/businesses/{b1}", headers={"X-CSRF-Token": csrf})
    assert delete_res.status_code == 204

    # В списке остаётся только второй бизнес
    remaining = (await client.get("/api/businesses")).json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == b2

    # Обращение к удалённому бизнесу возвращает 404
    accs_res = await client.get(f"/api/businesses/{b1}/accounts")
    assert accs_res.status_code == 404


async def test_delete_business_frees_cabinet(browser: Browser, provider: FakeProvider) -> None:
    client = browser()
    csrf = await _login(client, provider, ANNA)

    b1 = await _business(client, csrf, "Салон Пермь")
    b2 = await _business(client, csrf, "Салон Москва")

    # Подключаем кабинет к первому бизнесу
    connected = await _connect(client, provider, csrf, b1, ANNA)
    assert connected.headers["location"] == "/?connect=ok"

    # Попытка подключить тот же кабинет ко второму бизнесу отклоняется (taken)
    dup = await _connect(client, provider, csrf, b2, ANNA)
    assert dup.headers["location"] == "/?connect=taken"

    # Удаляем первый бизнес
    del_res = await client.delete(f"/api/businesses/{b1}", headers={"X-CSRF-Token": csrf})
    assert del_res.status_code == 204

    # Теперь тот же кабинет успешно подключается ко второму бизнесу!
    reconnected = await _connect(client, provider, csrf, b2, ANNA)
    assert reconnected.headers["location"] == "/?connect=ok"


async def test_delete_other_user_business_forbidden(
    browser: Browser, provider: FakeProvider
) -> None:
    anna_client = browser()
    anna_csrf = await _login(anna_client, provider, ANNA)
    anna_biz = await _business(anna_client, anna_csrf, "Салон Анны")

    boris_client = browser()
    boris_csrf = await _login(boris_client, provider, BORIS)

    # Борис пытается удалить бизнес Анны -> 404
    res = await boris_client.delete(
        f"/api/businesses/{anna_biz}", headers={"X-CSRF-Token": boris_csrf}
    )
    assert res.status_code == 404
