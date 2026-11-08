"""Мастер после входа: проверка кода Метрики и готовность к запуску."""

from unittest.mock import AsyncMock

import httpx
from sqlalchemy import text

from adhelper.budget.domain.limits import CampaignBudget
from adhelper.business.adapters.rules_file import load_tag_rules
from adhelper.business.adapters.tag_checks import SqlTagCheckQueue
from adhelper.business.application.tag import run_tag_checks
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import ANNA, BORIS, Browser, _connect, _login
from tests.accounts.test_marking import LEGAL_INN
from tests.api.test_cabinet import NOW, Sessions
from tests.api.test_site import Anna, anna  # noqa: F401
from tests.api.test_wizard_ads import written
from tests.budget.fake_platform import FakePlatform
from tests.business.test_tag import Pages

MICROS = 1_000_000

TAG_RULES = load_tag_rules()


async def _worker(sessions: Sessions, counters: list[int] | None) -> int:
    return await run_tag_checks(SqlTagCheckQueue(sessions), Pages(counters), TAG_RULES, lambda: NOW)


async def _profile(client: httpx.AsyncClient, csrf: str, business_id: object, site: str) -> None:
    body = {
        "niche": "маникюр",
        "address": "Москва, ул. Стромынка, 5",
        "services": [{"name": "Маникюр", "price": 1500}],
        "features": [],
        "website": site,
    }
    saved = await client.put(
        f"/api/businesses/{business_id}/profile", json=body, headers={"X-CSRF-Token": csrf}
    )
    assert saved.status_code == 200, saved.text


async def test_tag_check_finds_counter_on_profile_site(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}/tag-check"
    headers = {"X-CSRF-Token": csrf}
    assert (await client.get(base)).json() == {"check": None, "site": ""}
    refused = await client.post(base, headers=headers)
    assert refused.status_code == 422
    assert "сайта" in refused.json()["detail"]

    await _profile(client, csrf, business_id, "nogti.ru")
    queued = await client.post(base, headers=headers)
    assert queued.status_code == 202
    assert queued.json()["check"]["status"] == "queued"
    assert await _worker(sessions, [98765432]) == 1
    view = (await client.get(base)).json()
    assert view["site"] == "https://nogti.ru/"
    assert view["check"]["status"] == "found"
    assert view["check"]["counters"] == [98765432]
    assert (await client.post(base)).status_code == 403


async def test_missing_code_and_daily_limit(anna: Anna, sessions: Sessions) -> None:  # noqa: F811
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}/tag-check"
    headers = {"X-CSRF-Token": csrf}
    await _profile(client, csrf, business_id, "https://nogti.ru/")
    for _ in range(TAG_RULES.max_checks_per_day):
        assert (await client.post(base, headers=headers)).status_code == 202
        await _worker(sessions, [])
    check = (await client.get(base)).json()["check"]
    assert (check["status"], check["counters"]) == ("missing", [])
    limited = await client.post(base, headers=headers)
    assert limited.status_code == 429
    assert "завтра" in limited.json()["detail"]


async def test_other_owner_sees_nothing(
    anna: Anna,  # noqa: F811
    browser: Browser,
    provider: FakeProvider,
) -> None:
    _, _, business_id = anna
    other = browser()
    csrf = await _login(other, provider, BORIS)
    for path in ("tag-check", "launch-check"):
        assert (await other.get(f"/api/businesses/{business_id}/{path}")).status_code == 404
    post = await other.post(
        f"/api/businesses/{business_id}/tag-check", headers={"X-CSRF-Token": csrf}
    )
    assert post.status_code == 404


def _state_of(view: dict[str, object]) -> dict[str, bool]:
    return {item["key"]: item["ok"] for item in view["items"]}  # type: ignore[attr-defined]


async def _make_ready(
    client: httpx.AsyncClient,
    csrf: str,
    business_id: object,
    sessions: Sessions,
    provider: FakeProvider,
    rubles: int = 20_000,
    check_counter: bool = True,
) -> None:
    """Всё, что мастер собирает до запуска: профиль, бюджет, кабинет, код, объявления."""
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"
    await _profile(client, csrf, business_id, "nogti.ru")
    await client.put(f"{base}/budget-plan", json={"rubles": rubles}, headers=headers)
    await client.put(
        f"{base}/marking",
        json={"kind": "legal", "inn": LEGAL_INN, "name": "ООО «Ноготки»", "okved": "96.02"},
        headers=headers,
    )
    await _connect(client, provider, csrf, str(business_id), ANNA)
    if check_counter:
        await client.post(f"{base}/tag-check", headers=headers)
        await _worker(sessions, [98765432])
    set_id, token = await written(client, sessions)
    await client.post(
        f"{base}/creative-sets/adopt",
        json={"id": set_id, "token": token, "kept": [0, 1]},
        headers=headers,
    )


async def test_launch_check_lists_what_is_missing(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"

    empty = (await client.get(f"{base}/launch-check")).json()
    assert _state_of(empty) == {
        "profile": False,
        "plan": False,
        "account": False,
        "marking": False,
        "counter": True,
        "ads": False,
    }
    assert empty["ready"] is False
    assert empty["launch_available"] is False
    assert empty["launched"] is None

    await _make_ready(client, csrf, business_id, sessions, provider)

    full = (await client.get(f"{base}/launch-check")).json()
    # Когда всё собрано, сводка показывает, что именно создаст запуск.
    assert _state_of(full) == {**dict.fromkeys(_state_of(empty), True), "launch_plan": True}
    assert full["ready"] is True
    # Площадки нет (клиент Директа ждёт доступа): запуск честно недоступен.
    assert full["launch_available"] is False
    assert "откроет доступ" in full["launch_text"]
    texts = " ".join(item["text"] for item in full["items"]).replace("\xa0", " ")
    assert "20 000 ₽" in texts and "98765432" in texts and LEGAL_INN not in texts
    assert "Объявления: выбрано 2" in texts
    assert "Запуск: 1 кампания (поиск и сайты — 18 000 ₽)" in texts


async def test_launch_is_available_without_a_metrica_check(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider, check_counter=False)
    platform = FakePlatform([], NOW)
    _with_platform(client, platform)

    check = (await client.get(f"{base}/launch-check")).json()
    counter = next(item for item in check["items"] if item["key"] == "counter")
    assert counter["ok"] is True
    assert "обращения на сайте могут не учитываться" in counter["text"]
    assert check["launch_available"] is True

    launched = await client.post(f"{base}/launch", headers={"X-CSRF-Token": csrf})
    assert launched.status_code == 200, launched.text
    assert platform.create_calls == 1
    campaign = next(iter(platform.created.values())).campaign
    assert campaign.counters == ()


async def test_launch_does_not_reuse_counter_from_previous_site(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider)
    await _profile(client, csrf, business_id, "new-site.ru")
    platform = FakePlatform([], NOW)
    _with_platform(client, platform)

    check = (await client.get(f"{base}/launch-check")).json()
    counter = next(item for item in check["items"] if item["key"] == "counter")
    assert counter["ok"] is True
    assert "ещё не проверен" in counter["text"]

    launched = await client.post(f"{base}/launch", headers={"X-CSRF-Token": csrf})
    assert launched.status_code == 200, launched.text
    campaign = next(iter(platform.created.values())).campaign
    assert campaign.counters == ()


def _with_platform(client: httpx.AsyncClient, platform: FakePlatform | None) -> None:
    client._transport.app.state.platform_for = lambda _b, _a: platform  # type: ignore[attr-defined]


async def test_launch_uses_direct_dictionary_for_city_outside_shortlist(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider)
    await _profile(client, csrf, business_id, "nogti.ru")
    profile = (await client.get(f"{base}/profile")).json()
    profile["address"] = "г. Тольятти"
    await client.put(
        f"{base}/profile",
        json=profile,
        headers={"X-CSRF-Token": csrf},
    )
    platform = FakePlatform([], NOW)
    platform.resolve_city_region = AsyncMock(return_value=(999_999, "Тольятти"))  # type: ignore[attr-defined]
    _with_platform(client, platform)

    check = (await client.get(f"{base}/launch-check")).json()
    assert check["launch_available"] is True
    assert any("Город показа: Тольятти" in item["text"] for item in check["items"])

    result = await client.post(f"{base}/launch", headers={"X-CSRF-Token": csrf})
    assert result.status_code == 200, result.text
    campaign = next(iter(platform.created.values())).campaign
    assert campaign.regions == (999_999,)


async def test_launch_blocks_conflicting_profile_cities(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider)
    await _profile(client, csrf, business_id, "nogti.ru")
    profile = (await client.get(f"{base}/profile")).json()
    profile["address"] = "Москва, ул. Мира, г. Тольятти"
    await client.put(
        f"{base}/profile",
        json=profile,
        headers={"X-CSRF-Token": csrf},
    )
    platform = FakePlatform([], NOW)
    platform.resolve_city_region = AsyncMock(return_value=(999_999, "Тольятти"))  # type: ignore[attr-defined]
    _with_platform(client, platform)

    check = (await client.get(f"{base}/launch-check")).json()

    assert check["launch_available"] is False
    assert any("несколько городов" in item["text"] for item in check["items"])
    platform.resolve_city_region.assert_not_awaited()  # type: ignore[attr-defined]


async def test_launch_accepts_city_aliases_for_the_same_region(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider)
    await _profile(client, csrf, business_id, "nogti.ru")
    profile = (await client.get(f"{base}/profile")).json()
    profile["address"] = "Санкт-Петербург, ул. Мира, г. Петербург"
    await client.put(
        f"{base}/profile",
        json=profile,
        headers={"X-CSRF-Token": csrf},
    )
    platform = FakePlatform([], NOW)
    _with_platform(client, platform)

    check = (await client.get(f"{base}/launch-check")).json()

    assert check["launch_available"] is True
    assert any("Город показа: Санкт-Петербург" in item["text"] for item in check["items"])


async def test_launch_creates_campaign_and_shows_it_in_cabinet(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider)
    platform = FakePlatform([], NOW)
    _with_platform(client, platform)

    check = (await client.get(f"{base}/launch-check")).json()
    assert check["launch_available"] is True
    assert "Запустить рекламу" in check["launch_text"]
    assert (await client.post(f"{base}/launch")).status_code == 403

    done = await client.post(f"{base}/launch", headers=headers)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "done"
    assert [c["limit_rubles"] for c in body["campaigns"]] == [18_000]
    assert platform.create_calls == 1 and max(platform.limit_sums) <= 18_000 * MICROS

    after = (await client.get(f"{base}/launch-check")).json()
    assert (after["launched"], after["launch_available"]) == ("done", False)
    # Повторное нажатие ничего не создаёт.
    again = await client.post(f"{base}/launch", headers=headers)
    assert again.json()["status"] == "done" and platform.create_calls == 1

    # Кампания и объявления видны в кабинете до первой статистики.
    async with sessions() as session:
        states = (
            (
                await session.execute(
                    text("SELECT state FROM campaigns WHERE business_id = :b"), {"b": business_id}
                )
            )
            .scalars()
            .all()
        )
        ads = await session.scalar(
            text("SELECT count(*) FROM ads WHERE business_id = :b"), {"b": business_id}
        )
        kinds = (
            (
                await session.execute(
                    text("SELECT kind FROM changes WHERE business_id = :b ORDER BY id"),
                    {"b": business_id},
                )
            )
            .scalars()
            .all()
        )
    assert states == ["moderation"] and ads == 2
    # Маркировка ушла первой, кампании — после подтверждения чтением.
    assert kinds == ["set_advertiser", "create_campaign"]
    [sent] = platform.advertisers.values()
    assert (sent.kind, sent.inn, sent.okved) == ("legal", LEGAL_INN, "96.02")
    async with sessions() as session:
        journal = (
            await session.execute(
                text("SELECT planned::text, before::text, after::text FROM changes")
            )
        ).all()
    assert all(LEGAL_INN not in "".join(row) for row in journal)


async def test_launch_without_platform_records_nothing(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider)
    _with_platform(client, None)
    refused = await client.post(f"{base}/launch", headers={"X-CSRF-Token": csrf})
    assert refused.status_code == 503
    assert "откроет доступ" in refused.json()["detail"]
    async with sessions() as session:
        periods = await session.scalar(
            text("SELECT count(*) FROM budget_periods WHERE business_id = :b"), {"b": business_id}
        )
    assert periods == 0


async def test_launch_refuses_when_not_ready_or_over_budget(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"
    platform = FakePlatform([CampaignBudget("77", 10_000 * MICROS, 0, False, None)], NOW)
    _with_platform(client, platform)
    early = await client.post(f"{base}/launch", headers=headers)
    assert early.status_code == 409
    assert "В профиле нет услуг" in early.json()["detail"]

    await _make_ready(client, csrf, business_id, sessions, provider)
    # В кабинете уже есть чужая кампания с лимитом: вместе с новой вышли бы за бюджет.
    over = await client.post(f"{base}/launch", headers=headers)
    assert over.status_code == 409
    assert "Лимиты кампаний в кабинете Яндекса" in over.json()["detail"]
    assert platform.create_calls == 0


async def test_small_budget_says_minimum(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider, rubles=1_667)
    _with_platform(client, FakePlatform([], NOW))
    view = (await client.get(f"{base}/launch-check")).json()
    assert view["launch_available"] is True
    assert "1 500 ₽" in " ".join(i["text"] for i in view["items"]).replace("\xa0", " ")


async def test_other_owner_cannot_launch(
    anna: Anna,  # noqa: F811
    browser: Browser,
    provider: FakeProvider,
) -> None:
    _, _, business_id = anna
    other = browser()
    csrf = await _login(other, provider, BORIS)
    _with_platform(other, FakePlatform([], NOW))
    post = await other.post(f"/api/businesses/{business_id}/launch", headers={"X-CSRF-Token": csrf})
    assert post.status_code == 404


async def test_without_site_counter_is_not_required(anna: Anna) -> None:  # noqa: F811
    client, csrf, business_id = anna
    await _profile(client, csrf, business_id, "")
    view = (await client.get(f"/api/businesses/{business_id}/launch-check")).json()
    counter = next(item for item in view["items"] if item["key"] == "counter")
    assert counter["ok"] is True
    assert "звонк" in counter["text"]


async def test_launch_without_advertiser_returns_409_conflict(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    headers = {"X-CSRF-Token": csrf}
    await _make_ready(client, csrf, business_id, sessions, provider)
    async with sessions() as session:
        await session.execute(
            text("DELETE FROM advertiser_marking WHERE business_id = :b"),
            {"b": business_id},
        )
        await session.commit()
    res = await client.post(f"{base}/launch", headers=headers)
    assert res.status_code == 409
    assert "маркировки" in res.json()["detail"].lower()


async def test_launch_uses_fallback_region_when_city_not_found(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}"
    await _make_ready(client, csrf, business_id, sessions, provider)
    await _profile(client, csrf, business_id, "nogti.ru")
    profile = (await client.get(f"{base}/profile")).json()
    profile["address"] = "Пермь, ул. Комсомольский проспект, 71"
    await client.put(
        f"{base}/profile",
        json=profile,
        headers={"X-CSRF-Token": csrf},
    )
    platform = FakePlatform([], NOW)
    platform.resolve_city_region = AsyncMock(return_value=None)  # type: ignore[attr-defined]
    _with_platform(client, platform)

    check = (await client.get(f"{base}/launch-check")).json()
    assert check["launch_available"] is True
    assert any("Город показа: Россия" in item["text"] for item in check["items"])

    result = await client.post(f"{base}/launch", headers={"X-CSRF-Token": csrf})
    assert result.status_code == 200, result.text
    campaign = next(iter(platform.created.values())).campaign
    assert campaign.regions == (225,)
