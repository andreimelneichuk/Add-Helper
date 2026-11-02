"""Разбор сайта из кабинета: адрес проверяется до очереди, черновик читает только владелец."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.adapters.site_reads import SqlSiteReadQueue
from adhelper.business.application.site import run_site_reads
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import ANNA, BORIS, Browser, _business, _login
from tests.api.test_cabinet import NOW, Sessions
from tests.business.test_read_site import GOOD, HOME, PRICE, Model, Site

Anna = tuple[httpx.AsyncClient, str, uuid.UUID]
RULES = load_site_rules()


@pytest.fixture
async def anna(browser: Browser, provider: FakeProvider) -> AsyncIterator[Anna]:
    # Разбор сайта не требует кабинета Яндекса: только вход и бизнес.
    client = browser()
    csrf = await _login(client, provider, ANNA)
    business_id = uuid.UUID(await _business(client, csrf))
    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    transport.app.state.clock = lambda: NOW  # type: ignore[attr-defined]
    yield client, csrf, business_id


async def _worker(sessions: Sessions, model: Model | None) -> None:
    await run_site_reads(
        SqlSiteReadQueue(sessions),
        Site(HOME, PRICE),
        model,
        "qwen-test",
        RULES,
        lambda _: RULES,
        lambda: NOW,
    )


async def test_site_is_read_into_a_draft(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    headers = {"X-CSRF-Token": csrf}
    assert (await client.get(f"{base}/site-reads/latest")).json() is None

    queued = await client.post(f"{base}/site-reads", json={"url": "nogti.ru"}, headers=headers)
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert queued.json()["url"] == "https://nogti.ru/"

    await _worker(sessions, Model(GOOD))
    read = (await client.get(f"{base}/site-reads/latest")).json()
    assert read["status"] == "ready"
    assert read["problem_text"] is None
    assert read["facts"] == [
        {
            "field": "niche",
            "value": "маникюр",
            "price": None,
            "quote": "Студия маникюра «Ноготок»",
            "page": "https://nogti.ru/",
        },
        {
            "field": "service",
            "value": "Маникюр с покрытием",
            "price": 1500,
            "quote": "Маникюр с покрытием — 1 500 ₽",
            "page": "https://nogti.ru/price",
        },
    ]
    # Черновик не профиль: пока владелец не сохранил, профиль пуст.
    assert (await client.get(f"{base}/profile")).json()["services"] == []


async def test_failure_is_explained_in_owner_words(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await client.post(
        f"{base}/site-reads", json={"url": "https://nogti.ru"}, headers={"X-CSRF-Token": csrf}
    )
    await _worker(sessions, None)
    read = (await client.get(f"{base}/site-reads/latest")).json()
    assert (read["status"], read["problem"]) == ("failed", "no_model")
    assert "вручную" in read["problem_text"]


async def test_bad_and_internal_urls_are_refused_before_queue(anna: Anna) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    headers = {"X-CSRF-Token": csrf}
    for url, words in (
        ("ftp://nogti.ru", "http"),
        ("http://169.254.169.254/latest", "внутренн"),
        ("intranet", "не похоже"),
    ):
        response = await client.post(f"{base}/site-reads", json={"url": url}, headers=headers)
        assert response.status_code == 422, url
        assert words in response.json()["detail"], url
    assert (await client.get(f"{base}/site-reads/latest")).json() is None


async def test_daily_limit_is_reported(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    headers = {"X-CSRF-Token": csrf}
    for _ in range(RULES.max_reads_per_day):
        response = await client.post(
            f"{base}/site-reads", json={"url": "nogti.ru"}, headers=headers
        )
        assert response.status_code == 202
        await _worker(sessions, None)
    response = await client.post(f"{base}/site-reads", json={"url": "nogti.ru"}, headers=headers)
    assert response.status_code == 429
    assert "завтра" in response.json()["detail"]


async def test_other_owner_cannot_read_or_request(
    anna: Anna, browser: Browser, provider: FakeProvider
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await client.post(
        f"{base}/site-reads", json={"url": "nogti.ru"}, headers={"X-CSRF-Token": csrf}
    )
    boris = browser()
    boris_csrf = await _login(boris, provider, BORIS)
    assert (await boris.get(f"{base}/site-reads/latest")).status_code == 404
    response = await boris.post(
        f"{base}/site-reads", json={"url": "nogti.ru"}, headers={"X-CSRF-Token": boris_csrf}
    )
    assert response.status_code == 404
    # Без CSRF-ключа разбор не ставится даже владельцем.
    assert (await client.post(f"{base}/site-reads", json={"url": "nogti.ru"})).status_code == 403
