"""Экраны владельца через настоящий HTTP-слой и PostgreSQL: данные, доступ, CSRF."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.api.app import create_app
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.domain.balance import BalanceBaseline
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.watch import SpendSnapshot
from adhelper.config import Settings
from adhelper.notifications.adapters.sql import SqlAlerts
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import (
    ANNA,
    BASE,
    BORIS,
    Browser,
    _business,
    _connect,
    _login,
)

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
RUB = 1_000_000
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def anna(
    browser: Browser, provider: FakeProvider, sessions: Sessions
) -> AsyncIterator[tuple[httpx.AsyncClient, str, uuid.UUID]]:
    client = browser()
    csrf = await _login(client, provider, ANNA)
    business_id = uuid.UUID(await _business(client, csrf))
    await _connect(client, provider, csrf, str(business_id), ANNA)
    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    transport.app.state.clock = lambda: NOW  # type: ignore[attr-defined]
    yield client, csrf, business_id


async def _seed_money(sessions: Sessions, business_id: uuid.UUID) -> int:
    store = SqlPeriodStore(sessions)
    period_id = await store.add(
        business_id,
        "anna-direct",
        BudgetPeriod(
            30_000 * RUB,
            1_000,
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ),
        "budget-1",
        None,
        NOW,
    )
    await store.save_cycle(
        period_id, SpendSnapshot(9_000 * RUB, NOW - timedelta(hours=1)), False, NOW
    )
    # Отсчёт сверки снят при расходе 1000 ₽, со счёта ушло 9500 ₽ при расходе 8000 ₽.
    await store.save_balance(
        period_id, 40_500 * RUB, NOW, BalanceBaseline(50_000 * RUB, 1_000 * RUB)
    )
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO campaigns"
                " (business_id, account, campaign_id, name, state, limit_micros)"
                " VALUES (:b, 'anna-direct', '1', 'Маникюр — поиск', 'on', :l),"
                " (:b, 'anna-direct', '2', 'Педикюр — сети', 'suspended', NULL)"
            ),
            {"b": business_id, "l": 1_000 * RUB},
        )
        for offset, cost in ((0, 600), (1, 400), (30, 999)):
            await session.execute(
                text(
                    "INSERT INTO campaign_stats (business_id, account, campaign_id, day,"
                    " impressions, clicks, cost_micros, conversions)"
                    " VALUES (:b, 'anna-direct', '1', :d, 1000, 40, :c, 2)"
                ),
                {
                    "b": business_id,
                    "d": date(2026, 11, 10) - timedelta(days=offset),
                    "c": cost * RUB,
                },
            )
    return period_id


async def test_overview_shows_money_reconciliation_and_campaigns(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    client, _, business_id = anna
    await _seed_money(sessions, business_id)
    await SqlAlerts(sessions, business_id).raise_alert("balance_mismatch", {"gap": 1})

    body = (await client.get(f"/api/businesses/{business_id}/overview")).json()

    money = body["money"][0]
    assert money["period"]["spent"] == 9_000 * RUB
    assert money["spendable"] == 27_000 * RUB
    assert money["balance_gap"] == 1_500 * RUB
    assert money["balance_tolerance"] == 600 * RUB
    assert money["elapsed_bp"] == 3_125
    assert money["forecast"] == 9_000 * RUB * 10_000 // 3_125
    assert [a["kind"] for a in body["alerts"]] == ["balance_mismatch"]
    # День 30 дней назад в окно 14 дней не попадает.
    assert body["totals"] == {
        "impressions": 2000,
        "clicks": 80,
        "cost": 1_000 * RUB,
        "conversions": 4,
    }
    assert body["ratios"] == {
        "ctr_bp": 400,
        "cpc": 12_500_000,
        "cpa": 250 * RUB,
        "conversion_bp": 500,
    }
    names = [(c["name"], c["totals"]["clicks"]) for c in body["campaigns"]]
    assert names == [("Маникюр — поиск", 80), ("Педикюр — сети", 0)]
    assert body["campaigns"][1]["ratios"]["ctr_bp"] is None
    assert [d["day"] for d in body["days"]] == ["2026-11-09", "2026-11-10"]


async def test_owner_confirms_balance_and_alert_closes(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    client, csrf, business_id = anna
    url = f"/api/businesses/{business_id}/balance/confirm"
    headers = {"X-CSRF-Token": csrf}
    assert (
        await client.post(url, json={"account": "anna-direct"}, headers=headers)
    ).status_code == 409

    await _seed_money(sessions, business_id)
    await SqlAlerts(sessions, business_id).raise_alert("balance_mismatch", {})
    assert (await client.post(url, json={"account": "anna-direct"})).status_code == 403
    assert (
        await client.post(url, json={"account": "anna-direct"}, headers=headers)
    ).status_code == 200

    body = (await client.get(f"/api/businesses/{business_id}/overview")).json()
    assert body["alerts"] == []
    assert body["money"][0]["balance_gap"] == 0


async def test_profile_and_creative_sets(anna: tuple[httpx.AsyncClient, str, uuid.UUID]) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    headers = {"X-CSRF-Token": csrf}
    profile = {
        "services": [{"name": " Маникюр ", "price": 1500}, {"name": " ", "price": None}],
        "address": "ул. Ленина, 5",
        "features": ["Стерильные инструменты", ""],
        "niche": "маникюр",
        "website": "https://lak.example",
    }
    saved = (await client.put(f"{base}/profile", json=profile, headers=headers)).json()
    assert saved["name"] == "Салон"
    assert saved["services"] == [{"name": "Маникюр", "price": 1500}]
    assert saved["features"] == ["Стерильные инструменты"]

    for _ in range(5):
        assert (await client.post(f"{base}/creative-sets", headers=headers)).status_code == 202
    assert (await client.post(f"{base}/creative-sets", headers=headers)).status_code == 429
    listed = (await client.get(f"{base}/creative-sets")).json()
    assert [s["status"] for s in listed] == ["queued"] * 5
    # Черновиков у набора в очереди нет: решать не по чему.
    decision = await client.post(
        f"{base}/creative-sets/{listed[0]['id']}/drafts/0",
        json={"decision": "approved"},
        headers=headers,
    )
    assert decision.status_code == 404


async def test_other_owner_sees_nothing(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], browser: Browser, provider: FakeProvider
) -> None:
    _, _, business_id = anna
    boris = browser()
    csrf = await _login(boris, provider, BORIS)
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"
    for method, path in (
        ("GET", "/overview"),
        ("GET", "/history"),
        ("GET", "/profile"),
        ("GET", "/creative-sets"),
        ("POST", "/creative-sets"),
        ("POST", "/balance/confirm"),
    ):
        response = await boris.request(
            method, base + path, headers=headers, json={"account": "anna-direct"}
        )
        assert response.status_code == 404, path
    anonymous = browser()
    assert (await anonymous.get(base + "/overview")).status_code == 401


async def test_history_merges_changes_and_decisions(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    client, _, business_id = anna
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO decisions (business_id, account, object_ref, verdict, action, status,"
                " inputs, rules_version, decided_at)"
                " VALUES (:b, 'anna-direct', 'campaign:1', 'keep', NULL, 'active', '{}',"
                " 'autopilot-1', :t)"
            ),
            {"b": business_id, "t": NOW - timedelta(days=1)},
        )
    body = (await client.get(f"/api/businesses/{business_id}/history")).json()
    assert [(line["source"], line["kind"]) for line in body] == [("autopilot", "keep")]


async def test_login_without_provider_is_closed_but_sessions_work(
    api_settings: Settings,
) -> None:
    app = create_app(api_settings, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        assert (await client.get("/api/auth/yandex/login")).status_code == 503
        assert (await client.get("/api/me")).status_code == 401
    await app.state.engine.dispose()
