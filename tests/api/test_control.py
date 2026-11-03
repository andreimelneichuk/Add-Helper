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


RUB = 1_000_000


async def _hard_limit(sessions: Sessions, business_id: uuid.UUID) -> int:
    async with sessions() as session:
        value = await session.scalar(
            text("SELECT hard_limit_micros FROM budget_periods WHERE business_id = :b"),
            {"b": business_id},
        )
    assert isinstance(value, int)
    return value


async def test_owner_pause_is_lifted_with_budget_check(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    base = f"/api/businesses/{business}"
    await client.post(f"{base}/pause", json={"request_id": "r1"}, headers=csrf)
    response = await client.post(f"{base}/resume", json={"request_id": "r2"}, headers=csrf)

    assert response.status_code == 200
    assert response.json()["resumed"] == [f"{LOGIN}:1"]
    assert platform.campaigns["1"].active is True
    assert await _rows(sessions, business) == [
        ("user", "pause", "applied"),
        ("user", "resume", "applied"),
    ]
    overview = (await client.get(f"{base}/overview")).json()
    assert {c["state"] for c in overview["campaigns"] if c["campaign_id"] == "1"} == {"on"}


async def test_guard_stop_is_not_lifted_by_button(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    base = f"/api/businesses/{business}"
    await client.post(f"{base}/pause", json={"request_id": "r1"}, headers=csrf)
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO alerts (business_id, kind, details, created_at)"
                " VALUES (:b, 'budget_stopped', '{}'::jsonb, :t)"
            ),
            {"b": business, "t": NOW},
        )
    response = await client.post(f"{base}/resume", json={"request_id": "r2"}, headers=csrf)

    assert response.status_code == 409
    assert "бюджет месяца заканчивается" in response.json()["detail"]
    assert platform.campaigns["1"].active is False
    assert await _rows(sessions, business) == [("user", "pause", "applied")]


async def test_resume_without_owner_pause_is_refused(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    platform.campaigns["1"] = CampaignBudget("1", 100, 0, False, None)
    _app(client).state.platform_for = lambda _b, _a: platform
    response = await client.post(
        f"/api/businesses/{business}/resume", json={"request_id": "r1"}, headers=csrf
    )
    assert response.status_code == 409
    assert "остановил не вы" in response.json()["detail"]
    assert platform.campaigns["1"].active is False


async def test_budget_below_spent_is_refused_with_minimum(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    platform._history.append((NOW, 9_000 * RUB))
    _app(client).state.platform_for = lambda _b, _a: platform
    response = await client.post(
        f"/api/businesses/{business}/budget",
        json={"rubles": 5_000, "request_id": "r1"},
        headers=csrf,
    )
    assert response.status_code == 409
    assert "до 10 000 ₽" in response.json()["detail"]
    assert await _hard_limit(sessions, business) == 30_000 * RUB
    assert await _rows(sessions, business) == []


async def test_lowering_budget_cuts_limits_through_ledger_first(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    platform.campaigns["1"] = CampaignBudget("1", 25_000 * RUB, 0, True, None)
    _app(client).state.platform_for = lambda _b, _a: platform
    response = await client.post(
        f"/api/businesses/{business}/budget",
        json={"rubles": 10_000, "request_id": "r1"},
        headers=csrf,
    )
    assert response.status_code == 200
    assert response.json() == {"status": "applied", "cuts": 1}
    assert platform.campaigns["1"].limit <= 9_000 * RUB
    assert await _hard_limit(sessions, business) == 10_000 * RUB
    assert await _rows(sessions, business) == [("user", "set_limit", "applied")]


async def test_clients_answer_is_checked_against_leads_and_shown(anna: Anna) -> None:
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    week = {"since": "2026-11-04", "until": "2026-11-10"}
    too_many = await client.post(f"{base}/clients", json={**week, "clients": 5}, headers=csrf)
    assert too_many.status_code == 409
    assert too_many.json()["detail"] == "Обращений было 4, поэтому клиентов не больше 4"

    saved = await client.post(f"{base}/clients", json={**week, "clients": 3}, headers=csrf)
    assert saved.status_code == 200
    # Повторный ответ за то же окно заменяет прежний.
    await client.post(f"{base}/clients", json={**week, "clients": 2}, headers=csrf)
    overview = (await client.get(f"{base}/overview")).json()
    assert overview["clients"] == {**week, "leads": 4, "clients": 2}


async def test_clients_answer_needs_leads_and_past_dates(anna: Anna) -> None:
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    empty = {"since": "2026-10-01", "until": "2026-10-05", "clients": 1}
    assert (await client.post(f"{base}/clients", json=empty, headers=csrf)).status_code == 409
    future = {"since": "2026-11-10", "until": "2026-11-12", "clients": 1}
    assert (await client.post(f"{base}/clients", json=future, headers=csrf)).status_code == 422
    assert (await client.post(f"{base}/clients", json=empty)).status_code == 403


@pytest.fixture
async def both() -> FakePlatform:
    return FakePlatform(
        [CampaignBudget("1", 100, 0, True, None), CampaignBudget("2", 100, 0, False, None)], NOW
    )


async def test_schedule_change_goes_through_ledger_shows_and_undoes(
    anna: Anna, both: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: both
    base = f"/api/businesses/{business}"
    before = (await client.get(f"{base}/display")).json()
    assert before["schedule"] is None
    assert before["campaigns"] == 2
    assert {"id": 213, "name": "Москва"} in before["catalog"]

    body = {"days": [0, 1, 2, 3, 4, 5], "start": 10, "end": 21, "request_id": "s1"}
    response = await client.post(f"{base}/display/schedule", json=body, headers=csrf)
    assert response.status_code == 200
    assert response.json() == {"applied": [f"{LOGIN}:1", f"{LOGIN}:2"]}
    assert await _rows(sessions, business) == [("user", "set_schedule", "applied")] * 2
    shown = (await client.get(f"{base}/display")).json()
    assert shown["schedule"] == {"days": [0, 1, 2, 3, 4, 5], "start": 10, "end": 21}
    assert shown["schedule_text"] == "пн–сб, 10:00–21:00"

    history = (await client.get(f"{base}/history")).json()
    ref = next(h["ref"] for h in history if h["kind"] == "set_schedule")
    change_id = ref.removeprefix("change-")
    undo = await client.post(f"{base}/changes/{change_id}/rollback", headers=csrf)
    assert undo.status_code == 200
    # Отменили для одной кампании: у второй остались новые, общего значения нет.
    assert (await client.get(f"{base}/display")).json()["schedule"] is None


async def test_undo_shows_consequences_and_cancels_whole_request(
    anna: Anna, both: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: both
    base = f"/api/businesses/{business}"
    body = {"days": [0, 1, 2, 3, 4, 5], "start": 10, "end": 21, "request_id": "s1"}
    await client.post(f"{base}/display/schedule", json=body, headers=csrf)
    history = (await client.get(f"{base}/history")).json()
    change_id = next(h["ref"] for h in history if h["kind"] == "set_schedule")
    change_id = change_id.removeprefix("change-")

    preview = await client.get(f"{base}/changes/{change_id}/rollback")
    assert preview.status_code == 200
    shown = preview.json()
    assert shown["possible"] is True
    assert shown["directions"] == 2
    assert "Вернутся прежние часы показа: круглосуточно" in shown["text"]
    assert "Просьба меняла 2 направления" in shown["text"]
    # Предпросмотр ничего не меняет.
    assert await _rows(sessions, business) == [("user", "set_schedule", "applied")] * 2

    undo = await client.post(f"{base}/changes/{change_id}/rollback?whole=true", headers=csrf)
    assert undo.status_code == 200
    assert [r[0] for r in await _rows(sessions, business)].count("rollback") == 2
    assert (await client.get(f"{base}/display")).json()["schedule_text"] == "круглосуточно"
    lines = (await client.get(f"{base}/history")).json()
    assert [line["reverted"] for line in lines if line["source"] == "user"] == [True, True]

    again = (await client.get(f"{base}/changes/{change_id}/rollback")).json()
    assert again == {
        "possible": False,
        "status": "already_reverted",
        "text": "Это уже отменено",
        "directions": 0,
    }


async def test_undo_preview_is_owners_only(
    anna: Anna, browser: Browser, provider: FakeProvider
) -> None:
    _client, _csrf, business = anna
    other = browser()
    await _login(other, provider, BORIS)
    response = await other.get(f"/api/businesses/{business}/changes/1/rollback")
    assert response.status_code == 404


async def test_schedule_yandex_would_reject_is_refused_without_ledger(
    anna: Anna, both: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: both
    body = {"days": [6], "start": 10, "end": 17, "request_id": "s1"}
    response = await client.post(
        f"/api/businesses/{business}/display/schedule", json=body, headers=csrf
    )
    assert response.status_code == 422
    assert "8 часов показа в неделю" in response.json()["detail"]
    assert await _rows(sessions, business) == []


async def test_skip_days_keeps_hours_and_refuses_unknown_schedule(
    anna: Anna, both: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: both
    base = f"/api/businesses/{business}"
    skip = {"days": [6], "request_id": "k1"}
    # Часы направлений не прочитаны: дни не трогаем, чтобы не придумать расписание.
    unknown = await client.post(f"{base}/display/skip-days", json=skip, headers=csrf)
    assert unknown.status_code == 409
    assert "не прочитаны" in unknown.json()["detail"]
    assert await _rows(sessions, business) == []

    body = {"days": [0, 1, 2, 3, 4, 5, 6], "start": 9, "end": 21, "request_id": "s1"}
    await client.post(f"{base}/display/schedule", json=body, headers=csrf)
    done = await client.post(f"{base}/display/skip-days", json=skip, headers=csrf)
    assert done.status_code == 200, done.text
    assert done.json() == {"applied": [f"{LOGIN}:1", f"{LOGIN}:2"], "unknown": [], "unchanged": []}
    shown = (await client.get(f"{base}/display")).json()
    assert shown["schedule"] == {"days": [0, 1, 2, 3, 4, 5], "start": 9, "end": 21}

    again = await client.post(f"{base}/display/skip-days", json=skip, headers=csrf)
    # Витрина уже знает новое расписание: повтор ничего не меняет.
    assert again.json()["unchanged"] == [f"{LOGIN}:1", f"{LOGIN}:2"]
    too_short = {"days": [0, 1, 2, 3, 4, 5], "request_id": "k2"}
    refused = await client.post(f"{base}/display/skip-days", json=too_short, headers=csrf)
    assert refused.status_code == 422
    assert len(await _rows(sessions, business)) == 4


async def test_regions_change_and_platform_failure(
    anna: Anna, both: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: both
    url = f"/api/businesses/{business}/display/regions"
    both.unavailable = True
    down = await client.post(url, json={"regions": [2], "request_id": "g1"}, headers=csrf)
    assert down.status_code == 503
    both.unavailable = False
    done = await client.post(url, json={"regions": [2, 213], "request_id": "g2"}, headers=csrf)
    assert done.status_code == 200
    shown = (await client.get(f"/api/businesses/{business}/display")).json()
    assert shown["regions"] == [2, 213]
    assert shown["regions_text"] == "Санкт-Петербург, Москва"


async def test_display_change_without_platform_says_not_connected(
    anna: Anna, sessions: Sessions
) -> None:
    client, csrf, business = anna
    response = await client.post(
        f"/api/businesses/{business}/display/regions",
        json={"regions": [213], "request_id": "g1"},
        headers=csrf,
    )
    assert response.status_code == 503
    assert await _rows(sessions, business) == []


async def test_goal_is_stored_in_micros_and_shown(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    assert (await client.post(f"{base}/goal", json={"rubles": 0}, headers=csrf)).status_code == 422
    assert (await client.post(f"{base}/goal", json={"rubles": 450}, headers=csrf)).json() == {
        "goal": 450
    }
    assert (await client.get(f"{base}/display")).json()["goal"] == 450
    async with sessions() as session:
        stored = await session.scalar(
            text("SELECT goal_micros FROM lead_goals WHERE business_id = :b"), {"b": business}
        )
    assert stored == 450_000_000
    # Без CSRF цель не меняется.
    assert (await client.post(f"{base}/goal", json={"rubles": 1})).status_code == 403


async def test_display_belongs_to_owner_only(
    anna: Anna, browser: Browser, provider: FakeProvider
) -> None:
    _, _, business = anna
    other = browser()
    await _login(other, provider, BORIS)
    assert (await other.get(f"/api/businesses/{business}/display")).status_code == 404


async def test_today_cards_come_ordered_from_rules(
    anna: Anna, platform: FakePlatform, sessions: Sessions
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    base = f"/api/businesses/{business}"
    calm = (await client.get(f"{base}/today")).json()
    assert calm["rules"] == "today-1"
    assert calm["cards"][0]["kind"] == "summary"

    await client.post(f"{base}/pause", json={"request_id": "p1"}, headers=csrf)
    paused = (await client.get(f"{base}/today")).json()["cards"]
    assert paused[0]["kind"] == "paused"
    assert paused[0]["action"] == "resume"
    assert paused[0]["rules"] == "today-1"
    assert len(paused) <= 5


async def test_today_shows_recent_action_with_undo(anna: Anna, platform: FakePlatform) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda _b, _a: platform
    # Время записи журнала ставит база, поэтому часы сервиса берём настоящие.
    _app(client).state.clock = lambda: datetime.now(UTC)
    base = f"/api/businesses/{business}"
    await client.post(
        f"{base}/ads/{LOGIN}/11/state", json={"active": False, "request_id": "a1"}, headers=csrf
    )
    shown = (await client.get(f"{base}/today")).json()["cards"]
    last = next(card for card in shown if card["kind"] == "last_action")
    assert last["action"] == "undo"
    assert last["ref"].startswith("change-")
    assert last["text"].startswith("Вы попросили.")

    await client.post(
        f"{base}/changes/{last['ref'].removeprefix('change-')}/rollback", headers=csrf
    )
    after = (await client.get(f"{base}/today")).json()["cards"]
    assert "last_action" not in [card["kind"] for card in after]


async def test_today_is_owners_only(anna: Anna, browser: Browser, provider: FakeProvider) -> None:
    _client, _csrf, business = anna
    other = browser()
    await _login(other, provider, BORIS)
    assert (await other.get(f"/api/businesses/{business}/today")).status_code == 404
