"""Экономика бизнеса через HTTP: ориентир, подтверждение, цель по услугам, изоляция."""

from sqlalchemy import text

from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import BORIS, Browser, _login
from tests.api.test_chat import PROFILE
from tests.api.test_control import Anna, Sessions, _rows, anna, platform

__all__ = ["anna", "platform"]
URL = "/api/businesses/{}/economics"


async def test_without_profile_prices_there_is_no_goal_and_values_are_reference(
    anna: Anna, sessions: Sessions
) -> None:
    client, _, business = anna
    body = (await client.get(URL.format(business))).json()
    assert body["confirmed"] is False
    assert (body["margin_bp"], body["ad_share_bp"]) == (5000, 3000)
    assert body["goal_rubles"] is None and body["services"] == []
    async with sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM business_economics")) == 0


async def test_goal_is_counted_from_profile_prices_and_explained_with_numbers(anna: Anna) -> None:
    client, csrf, business = anna
    await client.put(f"/api/businesses/{business}/profile", json=PROFILE, headers=csrf)
    body = (await client.get(URL.format(business))).json()
    # Средний чек (1500 + 2000) / 2 = 1750 ₽; 1750 × 0,5 × 0,3 × 1,3 × 0,3 = 102,375 ₽.
    assert body["check_rubles"] == 1750 and body["check_from_profile"] is True
    assert body["goal_rubles"] == 102
    assert [(s["service"], s["goal_rubles"]) for s in body["services"]] == [
        ("Маникюр", 88),
        ("Педикюр", 117),
    ]
    assert "1 750 ₽" in body["how"] and "= 102 ₽ за обращение" in body["how"]
    assert body["confirmed"] is False


async def test_owner_check_replaces_profile_average_and_confirmation_is_explicit(
    anna: Anna,
) -> None:
    client, csrf, business = anna
    url = URL.format(business)
    done = await client.put(url, json={"avg_check_rubles": 3000, "confirm": True}, headers=csrf)
    assert done.status_code == 200
    body = done.json()
    assert (body["check_rubles"], body["check_from_profile"]) == (3000, False)
    assert body["goal_rubles"] == 175 or body["goal_rubles"] == 176  # 175,5 ₽
    assert body["confirmed"] is True
    # Смена любого числа без повторного подтверждения возвращает «ориентир».
    again = await client.put(url, json={"margin_bp": 4000}, headers=csrf)
    assert again.json()["confirmed"] is False
    assert (await client.get(url)).json()["confirmed"] is False
    # Подтверждение без изменений сохраняется.
    kept = await client.put(url, json={"confirm": True}, headers=csrf)
    assert kept.json()["confirmed"] is True
    assert (await client.put(url, json={}, headers=csrf)).json()["confirmed"] is True


async def test_bad_values_are_refused_in_owner_words(anna: Anna) -> None:
    client, csrf, business = anna
    url = URL.format(business)
    for body, word in (
        ({"margin_bp": 0}, "прибыль с чека"),
        ({"ad_share_bp": 10_001}, "доля прибыли на рекламу"),
        ({"repeat_bp": -1}, "повторные визиты"),
        ({"capacity_per_week": 0}, "сколько клиентов"),
        ({"avg_check_rubles": 0}, "средний чек"),
    ):
        answer = await client.put(url, json=body, headers=csrf)
        assert answer.status_code == 422, body
        assert word in answer.json()["detail"]


async def test_saving_economics_never_touches_the_platform(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business = anna
    before = await _rows(sessions, business)
    await client.put(URL.format(business), json={"avg_check_rubles": 2000}, headers=csrf)
    assert await _rows(sessions, business) == before


async def test_other_users_business_is_not_found(
    anna: Anna, browser: Browser, provider: FakeProvider
) -> None:
    client, csrf, business = anna
    await client.put(URL.format(business), json={"avg_check_rubles": 2000}, headers=csrf)
    boris = browser()
    boris_csrf = await _login(boris, provider, BORIS)
    headers = {"X-CSRF-Token": boris_csrf}
    assert (await boris.get(URL.format(business))).status_code == 404
    answer = await boris.put(URL.format(business), json={"confirm": True}, headers=headers)
    assert answer.status_code == 404
    assert (await client.get(URL.format(business))).json()["confirmed"] is False


async def test_saved_numbers_are_read_back(anna: Anna) -> None:
    client, csrf, business = anna
    url = URL.format(business)
    body = {"margin_bp": 4000, "lead_to_client_bp": 2500, "confirm": True}
    assert (await client.put(url, json=body, headers=csrf)).status_code == 200
    again = (await client.get(url)).json()
    assert (again["margin_bp"], again["lead_to_client_bp"], again["confirmed"]) == (
        4000,
        2500,
        True,
    )


async def test_numbers_with_profile_prices_are_read_back(anna: Anna) -> None:
    client, csrf, business = anna
    await client.put(f"/api/businesses/{business}/profile", json=PROFILE, headers=csrf)
    url = URL.format(business)
    body = {"lead_to_client_bp": 3000, "margin_bp": 4000, "repeat_bp": 3000, "ad_share_bp": 3000}
    done = (await client.put(url, json={**body, "confirm": True}, headers=csrf)).json()
    assert done["margin_bp"] == 4000
    assert (await client.get(url)).json()["margin_bp"] == 4000
