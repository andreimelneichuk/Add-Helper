"""Разбор сайта до входа: шаг 1 мастера, без учётной записи, с лимитом по адресу."""

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from adhelper.api.app import create_app
from adhelper.config import Settings
from tests.api.test_cabinet import NOW, Sessions
from tests.api.test_site import RULES, _worker
from tests.business.test_read_site import GOOD, Model

Guest = Callable[[str], httpx.AsyncClient]
BASE = "https://adhelper.test"


@pytest.fixture
async def guest(migrated: Settings) -> AsyncIterator[Guest]:
    app = create_app(migrated)
    app.state.clock = lambda: NOW
    clients: list[httpx.AsyncClient] = []

    def open_from(address: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(address, 40000))
        client = httpx.AsyncClient(transport=transport, base_url=BASE)
        clients.append(client)
        return client

    yield open_from
    for client in clients:
        await client.aclose()
    await app.state.engine.dispose()


async def test_guest_reads_site_without_login(guest: Guest, sessions: Sessions) -> None:
    client = guest("203.0.113.5")
    queued = await client.post("/api/site-reads", json={"url": "nogti.ru"})
    assert queued.status_code == 202
    body = queued.json()
    assert (body["status"], body["url"]) == ("queued", "https://nogti.ru/")
    token = body["token"]
    assert len(token) >= 32

    path = f"/api/site-reads/{body['id']}"
    assert (await client.get(path, headers={"X-Read-Token": token})).json()["status"] == "queued"
    await _worker(sessions, Model(GOOD))
    read = (await client.get(path, headers={"X-Read-Token": token})).json()
    assert read["status"] == "ready"
    assert [f["value"] for f in read["facts"]] == ["маникюр", "Маникюр с покрытием"]
    assert read["facts"][1]["quote"] == "Маникюр с покрытием — 1 500 ₽"

    # Без ключа или с чужим ключом черновика не видно.
    assert (await client.get(path)).status_code == 404
    assert (await client.get(path, headers={"X-Read-Token": "x" * 32})).status_code == 404


async def test_guest_url_is_checked_before_queue(guest: Guest) -> None:
    client = guest("203.0.113.5")
    for url, words in (("http://10.0.0.1/", "внутренн"), ("intranet", "не похоже")):
        response = await client.post("/api/site-reads", json={"url": url})
        assert response.status_code == 422, url
        assert words in response.json()["detail"], url


async def test_guest_limit_is_per_address(guest: Guest, sessions: Sessions) -> None:
    first = guest("203.0.113.5")
    for _ in range(RULES.max_guest_reads_per_address):
        assert (await first.post("/api/site-reads", json={"url": "nogti.ru"})).status_code == 202
        await _worker(sessions, None)
    refused = await first.post("/api/site-reads", json={"url": "nogti.ru"})
    assert refused.status_code == 429
    assert "вручную" in refused.json()["detail"]
    other = guest("198.51.100.7")
    assert (await other.post("/api/site-reads", json={"url": "nogti.ru"})).status_code == 202


async def test_ipv6_neighbours_share_one_limit(guest: Guest, sessions: Sessions) -> None:
    # Одна сеть /64 у одного абонента: смена последних групп адреса лимит не обходит.
    for n in range(RULES.max_guest_reads_per_address):
        client = guest(f"2001:db8:1:2::{n + 1}")
        assert (await client.post("/api/site-reads", json={"url": "nogti.ru"})).status_code == 202
    late = guest("2001:db8:1:2:ffff::9")
    assert (await late.post("/api/site-reads", json={"url": "nogti.ru"})).status_code == 429


async def test_form_post_from_other_site_is_not_accepted(guest: Guest) -> None:
    # Чужая страница без предварительного запроса может отправить только форму, не JSON.
    client = guest("203.0.113.5")
    response = await client.post("/api/site-reads", data={"url": "nogti.ru"})
    assert response.status_code == 422
