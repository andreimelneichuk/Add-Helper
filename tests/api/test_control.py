"""Действия владельца идут через журнал: docs/10-redesign.md, раздел R7, инвариант единого пути."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.budget.domain.limits import CampaignBudget
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import ANNA, BORIS, Browser, _business, _connect, _login
from tests.api.test_cabinet import _seed_ads, _seed_money
from tests.budget.fake_platform import FakePlatform

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
LOGIN = "anna-direct"
Sessions = async_sessionmaker[AsyncSession]
Anna = tuple[httpx.AsyncClient, dict[str, str], uuid.UUID]


def _app(client: httpx.AsyncClient):  # type: ignore[no-untyped-def]
    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    return transport.app


@pytest.fixture
async def platform() -> FakePlatform:
    fake = FakePlatform([CampaignBudget("1", 100, 0, True, None)], NOW)
    fake.ads = {"ad:11": True, "ad:12": False}
    return fake


@pytest.fixture
async def anna(browser: Browser, provider: FakeProvider, sessions: Sessions) -> AsyncIterator[Anna]:
    client = browser()
    csrf = await _login(client, provider, ANNA)
    business_id = uuid.UUID(await _business(client, csrf))
    await _connect(client, provider, csrf, str(business_id), ANNA)
    _app(client).state.clock = lambda: NOW
    await _seed_money(sessions, business_id)
    await _seed_ads(sessions, business_id)
    yield client, {"X-CSRF-Token": csrf}, business_id


async def _rows(sessions: Sessions, business_id: uuid.UUID) -> list[tuple[str, str, str]]:
    async with sessions() as session:
        rows = await session.execute(
            text("SELECT source, kind, status FROM changes WHERE business_id = :b ORDER BY id"),
            {"b": business_id},
        )
    return [(r[0], r[1], r[2]) for r in rows]


async def test_ad_off_goes_through_ledger_and_reads_result(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    done = await client.post(
        f"/api/businesses/{business}/ads/{LOGIN}/11/state",
        json={"active": False, "request_id": "r1"},
        headers=csrf,
    )
    assert done.status_code == 200
    assert done.json() == {"status": "applied"}
    assert platform.ads["ad:11"] is False
    assert await _rows(sessions, business) == [("user", "pause_ad", "applied")]


async def test_repeated_request_does_not_change_twice(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    url = f"/api/businesses/{business}/ads/{LOGIN}/11/state"
    for _ in range(2):
        response = await client.post(url, json={"active": False, "request_id": "r1"}, headers=csrf)
        assert response.status_code == 200
    assert len(await _rows(sessions, business)) == 1


async def test_without_platform_nothing_is_recorded_and_says_so(
    anna: Anna, sessions: Sessions
) -> None:
    client, csrf, business = anna
    for url, body in (
        (f"ads/{LOGIN}/11/state", {"active": False, "request_id": "r1"}),
        ("pause", {"request_id": "r2"}),
    ):
        response = await client.post(f"/api/businesses/{business}/{url}", json=body, headers=csrf)
        assert response.status_code == 503
        assert "не подключ" in response.json()["detail"]
    assert await _rows(sessions, business) == []


async def test_pause_stops_active_campaigns_through_ledger(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    response = await client.post(
        f"/api/businesses/{business}/pause", json={"request_id": "r1"}, headers=csrf
    )
    assert response.status_code == 200
    assert platform.campaigns["1"].active is False
    assert await _rows(sessions, business) == [("user", "pause", "applied")]


async def test_rollback_makes_new_record(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    base = f"/api/businesses/{business}"
    await client.post(
        f"{base}/ads/{LOGIN}/11/state", json={"active": False, "request_id": "r1"}, headers=csrf
    )
    async with sessions() as session:
        change = (await session.execute(text("SELECT max(id) FROM changes"))).scalar_one()
    done = await client.post(f"{base}/changes/{change}/rollback", headers=csrf)
    assert done.status_code == 200
    assert platform.ads["ad:11"] is True
    assert [row[1] for row in await _rows(sessions, business)] == ["pause_ad", "resume_ad"]
    again = await client.post(f"{base}/changes/{change}/rollback", headers=csrf)
    assert again.status_code == 409


async def test_other_business_gets_404_and_csrf_is_required(
    anna: Anna, browser: Browser, provider: FakeProvider, platform: FakePlatform
) -> None:
    client, _csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    url = f"/api/businesses/{business}/ads/{LOGIN}/11/state"
    body = {"active": False, "request_id": "r1"}
    assert (await client.post(url, json=body)).status_code in (401, 403)
    boris = browser()
    other = await _login(boris, provider, BORIS)
    response = await boris.post(url, json=body, headers={"X-CSRF-Token": other})
    assert response.status_code == 404
    assert platform.ads["ad:11"] is True


async def test_unknown_ad_is_404(anna: Anna, platform: FakePlatform) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    response = await client.post(
        f"/api/businesses/{business}/ads/{LOGIN}/999/state",
        json={"active": False, "request_id": "r1"},
        headers=csrf,
    )
    assert response.status_code == 404


async def _ad_state(client: httpx.AsyncClient, business: uuid.UUID) -> str:
    ads = (await client.get(f"/api/businesses/{business}/ads")).json()
    return str(next(ad["state"] for ad in ads if ad["ad_id"] == "11"))


async def test_screens_see_the_result_after_reload(anna: Anna, platform: FakePlatform) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    base = f"/api/businesses/{business}"
    await client.post(
        f"{base}/ads/{LOGIN}/11/state", json={"active": False, "request_id": "r1"}, headers=csrf
    )
    assert await _ad_state(client, business) == "suspended"

    history = (await client.get(f"{base}/history")).json()
    change = next(line["ref"] for line in history if line["kind"] == "pause_ad")
    await client.post(f"{base}/changes/{change.removeprefix('change-')}/rollback", headers=csrf)
    assert await _ad_state(client, business) == "on"


async def test_pause_is_visible_on_campaigns(anna: Anna, platform: FakePlatform) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    base = f"/api/businesses/{business}"
    await client.post(f"{base}/pause", json={"request_id": "r1"}, headers=csrf)
    overview = (await client.get(f"{base}/overview")).json()
    assert {c["state"] for c in overview["campaigns"] if c["campaign_id"] == "1"} == {"suspended"}
