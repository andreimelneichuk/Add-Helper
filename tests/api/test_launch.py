"""Мастер после входа: проверка кода Метрики и готовность к запуску."""

import httpx

from adhelper.business.adapters.rules_file import load_tag_rules
from adhelper.business.adapters.tag_checks import SqlTagCheckQueue
from adhelper.business.application.tag import run_tag_checks
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import ANNA, BORIS, Browser, _connect, _login
from tests.accounts.test_marking import LEGAL_INN
from tests.api.test_cabinet import NOW, Sessions
from tests.api.test_site import Anna, anna  # noqa: F401
from tests.business.test_tag import Pages

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


async def test_launch_check_lists_what_is_missing(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"

    def state(view: dict[str, object]) -> dict[str, bool]:
        return {item["key"]: item["ok"] for item in view["items"]}  # type: ignore[attr-defined]

    empty = (await client.get(f"{base}/launch-check")).json()
    assert state(empty) == {
        "profile": False,
        "plan": False,
        "account": False,
        "marking": False,
        "counter": False,
    }
    assert empty["ready"] is False
    # Запуск сам кампании пока не создаёт: это честно сказано, даже когда всё готово.
    assert empty["launch_available"] is False

    await _profile(client, csrf, business_id, "nogti.ru")
    await client.put(f"{base}/budget-plan", json={"rubles": 20_000}, headers=headers)
    await client.put(
        f"{base}/marking",
        json={"kind": "legal", "inn": LEGAL_INN, "name": "ООО «Ноготки»", "okved": "96.02"},
        headers=headers,
    )
    await _connect(client, provider, csrf, str(business_id), ANNA)
    await client.post(f"{base}/tag-check", headers=headers)
    await _worker(sessions, [98765432])

    full = (await client.get(f"{base}/launch-check")).json()
    assert state(full) == dict.fromkeys(state(empty), True)
    assert full["ready"] is True
    assert full["launch_available"] is False
    texts = " ".join(item["text"] for item in full["items"]).replace("\xa0", " ")
    assert "20 000 ₽" in texts and "98765432" in texts and LEGAL_INN not in texts


async def test_without_site_counter_is_not_required(anna: Anna) -> None:  # noqa: F811
    client, csrf, business_id = anna
    await _profile(client, csrf, business_id, "")
    view = (await client.get(f"/api/businesses/{business_id}/launch-check")).json()
    counter = next(item for item in view["items"] if item["key"] == "counter")
    assert counter["ok"] is True
    assert "звонк" in counter["text"]
