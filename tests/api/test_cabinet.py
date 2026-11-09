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
from adhelper.platforms.domain.ports import AdDay, AdInventory, CampaignDay, CampaignInventory
from adhelper.stats.adapters.sql import SqlReportStats
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


async def test_overview_uses_ad_report_when_campaign_report_is_missing(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    client, _, business_id = anna
    await _seed_ads(sessions, business_id)
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO campaigns"
                " (business_id, account, campaign_id, name, state)"
                " VALUES (:b, 'anna-direct', '1', 'Маникюр — поиск', 'on')"
            ),
            {"b": business_id},
        )

    body = (
        await client.get(
            f"/api/businesses/{business_id}/overview",
            params={"since": "2026-11-09", "until": "2026-11-10"},
        )
    ).json()

    campaign = next(c for c in body["campaigns"] if c["campaign_id"] == "1")
    assert campaign["totals"] == {
        "impressions": 1500,
        "clicks": 40,
        "cost": 400 * RUB,
        "conversions": 3,
    }
    assert body["totals"] == campaign["totals"]
    assert [day["totals"]["clicks"] for day in body["days"]] == [10, 30]


async def test_direct_report_rows_are_upserted_for_dashboard_and_ads(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    _client, _csrf, business_id = anna
    sink = SqlReportStats(sessions, business_id)
    campaign = CampaignDay("42", date(2026, 11, 9), 2762, 24, 57_550_000, 12)
    ad = AdDay("1926471958164085430", date(2026, 11, 9), 2762, 24, 57_550_000, 12)

    assert await sink.save("anna-direct", [campaign], [ad]) == 2
    assert await sink.save("anna-direct", [campaign], [ad]) == 2

    async with sessions() as session:
        counts = await session.execute(
            text(
                "SELECT (SELECT count(*) FROM campaign_stats WHERE business_id = :b),"
                " (SELECT count(*) FROM ad_stats WHERE business_id = :b)"
            ),
            {"b": business_id},
        )
        assert counts.one() == (1, 1)


async def test_direct_inventory_sync_updates_states_and_inserts_new_ads(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    _client, _csrf, business_id = anna
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO campaigns"
                " (business_id, account, campaign_id, name, state)"
                " VALUES (:b, 'anna-direct', '42', 'Old campaign name', 'moderation')"
            ),
            {"b": business_id},
        )
        await session.execute(
            text(
                "INSERT INTO ads"
                " (business_id, account, ad_id, campaign_id, title, text, href, state)"
                " VALUES (:b, 'anna-direct', 'old-ad', '42', 'Old title', 'Old text',"
                " 'https://example.com/', 'moderation')"
            ),
            {"b": business_id},
        )

    await SqlReportStats(sessions, business_id).sync_inventory(
        "anna-direct",
        [CampaignInventory("42", "Campaign from Direct", "on")],
        [
            AdInventory(
                "old-ad", "42", "Updated title", "Updated text", "https://example.com/", "on"
            ),
            AdInventory("new-ad", "42", "New ad", "New text", "https://example.com/", "moderation"),
            AdInventory(
                "archived-ad", "42", "Old duplicate", "Old text", "https://example.com/", "archived"
            ),
        ],
    )

    async with sessions() as session:
        campaign = (
            await session.execute(
                text(
                    "SELECT name, state FROM campaigns"
                    " WHERE business_id = :b AND campaign_id = '42'"
                ),
                {"b": business_id},
            )
        ).one()
        ads = (
            await session.execute(
                text(
                    "SELECT ad_id, title, state FROM ads"
                    " WHERE business_id = :b AND campaign_id = '42' ORDER BY ad_id"
                ),
                {"b": business_id},
            )
        ).all()
    assert campaign == ("Campaign from Direct", "on")
    assert ads == [
        ("archived-ad", "Old duplicate", "archived"),
        ("new-ad", "New ad", "moderation"),
        ("old-ad", "Updated title", "on"),
    ]


async def test_overview_shows_saved_account_balance_when_no_periods(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    client, _, business_id = anna
    async with sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE ad_accounts SET last_balance_micros = 15000000000, "
                "balance_checked_at = NOW() WHERE business_id = :b"
            ),
            {"b": business_id},
        )
    body = (await client.get(f"/api/businesses/{business_id}/overview")).json()
    assert body["account_balance_micros"] == 15_000_000_000
    assert body["account_login"] == "anna-direct"

    # Граничный случай: 0 ₽ является валидным балансом и не превращается в None
    async with sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE ad_accounts SET last_balance_micros = 0, "
                "balance_checked_at = NOW() WHERE business_id = :b"
            ),
            {"b": business_id},
        )
    body = (await client.get(f"/api/businesses/{business_id}/overview")).json()
    assert body["account_balance_micros"] == 0


async def _seed_ads(sessions: Sessions, business_id: uuid.UUID) -> None:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO ads"
                " (business_id, account, ad_id, campaign_id, title, text, href, state)"
                " VALUES (:b, 'anna-direct', '11', '1', 'Маникюр 1500 ₽', 'У метро',"
                " 'https://a.example', 'on'),"
                " (:b, 'anna-direct', '12', '1', 'Маникюр рядом', 'Онлайн-запись', '',"
                " 'suspended')"
            ),
            {"b": business_id},
        )
        for ad_id, offset, clicks in (("11", 0, 30), ("11", 1, 10), ("12", 0, 0), ("11", 20, 5)):
            await session.execute(
                text(
                    "INSERT INTO ad_stats (business_id, account, ad_id, day,"
                    " impressions, clicks, cost_micros, conversions)"
                    " VALUES (:b, 'anna-direct', :a, :d, 500, :k, :c, 1)"
                ),
                {
                    "b": business_id,
                    "a": ad_id,
                    "d": date(2026, 11, 10) - timedelta(days=offset),
                    "k": clicks,
                    "c": clicks * 10 * RUB,
                },
            )


async def test_overview_for_chosen_period_compares_with_previous(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    client, _, business_id = anna
    await _seed_money(sessions, business_id)
    base = f"/api/businesses/{business_id}/overview"

    body = (await client.get(base, params={"since": "2026-11-10", "until": "2026-11-10"})).json()
    assert (body["since"], body["until"]) == ("2026-11-10", "2026-11-10")
    assert body["totals"]["cost"] == 600 * RUB
    # Предыдущий день того же размера — 9 ноября.
    assert body["previous"]["cost"] == 400 * RUB
    assert [d["day"] for d in body["days"]] == ["2026-11-10"]

    month = (await client.get(base, params={"since": "2026-10-01", "until": "2026-11-10"})).json()
    assert month["totals"]["cost"] == 1_999 * RUB

    for params in (
        {"since": "2026-11-10", "until": "2026-11-09"},
        {"until": "2026-11-11"},
        {"since": "2025-11-01", "until": "2026-11-10"},
    ):
        assert (await client.get(base, params=params)).status_code == 422, params


async def test_campaign_and_ad_dashboards(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    client, _, business_id = anna
    await _seed_money(sessions, business_id)
    await _seed_ads(sessions, business_id)
    base = f"/api/businesses/{business_id}"

    campaign = (await client.get(base + "/campaigns/anna-direct/1")).json()
    assert campaign["campaign"]["name"] == "Маникюр — поиск"
    assert campaign["campaign"]["totals"]["cost"] == 1_000 * RUB
    assert campaign["ratios"]["cpa"] == 250 * RUB
    assert [d["day"] for d in campaign["days"]] == ["2026-11-09", "2026-11-10"]
    assert [(a["ad_id"], a["totals"]["clicks"]) for a in campaign["ads"]] == [
        ("11", 40),
        ("12", 0),
    ]
    assert campaign["ads"][1]["ratios"]["ctr_bp"] == 0

    ad = (await client.get(base + "/ads/anna-direct/11", params={"since": "2026-11-10"})).json()
    assert ad["ad"]["title"] == "Маникюр 1500 ₽"
    assert ad["campaign_name"] == "Маникюр — поиск"
    assert ad["ad"]["totals"]["clicks"] == 30
    assert ad["previous"]["clicks"] == 10
    assert [d["day"] for d in ad["days"]] == ["2026-11-10"]
    # Профиля нет: число из объявления подтвердить нечем, и экран это покажет.
    assert ad["basis"] == {"sources": [], "unconfirmed": ["1500"]}
    assert ad["audience"]["regions"] == []

    every = (await client.get(base + "/ads")).json()
    assert [a["ad_id"] for a in every] == ["11", "12"]
    assert every[0]["ratios"]["cpc"] == 10 * RUB

    assert (await client.get(base + "/campaigns/anna-direct/404")).status_code == 404
    assert (await client.get(base + "/ads/other-login/11")).status_code == 404


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


async def test_profile_and_creatives(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
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

    creative_id = await _seed_creative(sessions, business_id, "needs_owner")
    listed = (await client.get(f"{base}/creatives")).json()
    assert [(c["id"], c["campaign_name"]) for c in listed["creatives"]] == [
        (creative_id, "Маникюр — поиск")
    ]
    assert listed["writing"][0]["reason"] == "refill"

    report = (await client.get(f"{base}/creatives/{creative_id}")).json()
    assert report["audience"]["regions"] == ["Казань"]
    assert report["basis"]["unconfirmed"] == []
    assert "цена услуги «Маникюр»" in [s["label"] for s in report["basis"]["sources"]]

    without_csrf = await client.post(f"{base}/creatives/{creative_id}/launch")
    assert without_csrf.status_code == 403
    launched = await client.post(f"{base}/creatives/{creative_id}/launch", headers=headers)
    assert launched.json()["creative"]["state"] == "launch_pending"
    again = await client.post(f"{base}/creatives/{creative_id}/launch", headers=headers)
    assert again.status_code == 409
    withdrawn = await client.post(f"{base}/creatives/{creative_id}/withdraw", headers=headers)
    assert withdrawn.json()["creative"]["state_reason"] == "owner"
    assert (await client.get(f"{base}/creatives/999999")).status_code == 404


async def _seed_creative(sessions: Sessions, business_id: uuid.UUID, state: str) -> int:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO campaigns (business_id, account, campaign_id, name, state, targeting)"
                " VALUES (:b, 'anna-direct', '1', 'Маникюр — поиск', 'on',"
                ' \'{"regions": ["Казань"]}\')'
            ),
            {"b": business_id},
        )
        await session.execute(
            text(
                "INSERT INTO creative_sets (business_id, status, rules_version, attempts,"
                " drafts, rejected, notes, tokens_used, needs_review, created_at,"
                " account, campaign_id, reason, wanted)"
                " VALUES (:b, 'queued', 'creatives-2', 0, '[]', '[]', '[]', 0, false, :now,"
                " 'anna-direct', '1', 'refill', 1)"
            ),
            {"b": business_id, "now": NOW},
        )
        found = await session.scalar(
            text(
                "INSERT INTO creatives (business_id, account, campaign_id, headline, text,"
                " source, auto_ok, reason, facts, sources, state, state_reason, history,"
                " created_at, updated_at)"
                " VALUES (:b, 'anna-direct', '1', 'Маникюр за 1500 ₽', 'Ул. Ленина, 5.',"
                " 'model', false, 'new_campaign', '[]', '[]', :state, 'place_free', '[]',"
                " :now, :now) RETURNING id"
            ),
            {"b": business_id, "state": state, "now": NOW},
        )
    return int(found)


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
        ("GET", "/creatives"),
        ("GET", "/creatives/1"),
        ("GET", "/ads"),
        ("GET", "/campaigns/anna-direct/1"),
        ("GET", "/ads/anna-direct/11"),
        ("POST", "/creatives/1/launch"),
        ("POST", "/creatives/1/withdraw"),
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


async def test_history_shows_failure_reason_of_a_refused_change(
    anna: tuple[httpx.AsyncClient, str, uuid.UUID], sessions: Sessions
) -> None:
    # Регрессия: колонка failure_reason не читалась в запросе истории, и /history и /today
    # отвечали 500 на любом неуспешном изменении.
    client, _, business_id = anna
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO changes (business_id, account, source, kind, target_ref, before,"
                " planned, after, status, explanation, idempotency_key, failure_reason,"
                " created_at, updated_at)"
                " VALUES (:b, 'anna-direct', 'user', 'create_campaign', 'camp:9', '{}', '{}',"
                " NULL, 'failed', 'Создал кампанию', 'test-failed-1', 'exceeds_operating_budget',"
                " :t, :t)"
            ),
            {"b": business_id, "t": NOW - timedelta(hours=1)},
        )
    response = await client.get(f"/api/businesses/{business_id}/history")
    assert response.status_code == 200
    failed = [line for line in response.json() if line["status"] == "failed"]
    assert len(failed) == 1
    assert "превысили бы рабочую часть бюджета" in failed[0]["failure"]


async def test_login_without_provider_is_closed_but_sessions_work(
    api_settings: Settings,
) -> None:
    app = create_app(api_settings, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        assert (await client.get("/api/auth/yandex/login")).status_code == 503
        assert (await client.get("/api/me")).status_code == 401
    await app.state.engine.dispose()
