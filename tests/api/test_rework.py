"""Переделка объявления по просьбе: HTTP → проверка просьбы → очередь → воркер → выбор."""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import text

from adhelper.creatives.adapters.rules_file import load_rework_rules, load_rules
from adhelper.creatives.adapters.sql import SqlCreativeQueue, SqlProfileSource
from adhelper.creatives.application.service import run_queued
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import BORIS, Browser, _login
from tests.api.test_control import Anna, Sessions, anna, platform  # noqa: F401
from tests.creatives.test_generate import FakeModel, ads

PROFILE = {
    "niche": "маникюр",
    "address": "ул. Ленина, 5",
    "services": [{"name": "Маникюр", "price": 1500}],
    "features": ["Стерильные инструменты"],
    "website": "",
}
SHORT = {"headline": "Маникюр за 1500 ₽", "text": "Ждём вас: ул. Ленина, 5."}
PLACE = {"headline": "Маникюр на Ленина, 5", "text": "Стерильные инструменты, рядом с вами."}


async def _write(sessions: Sessions, model: FakeModel | None) -> None:
    await run_queued(
        SqlCreativeQueue(sessions),
        SqlProfileSource(sessions),
        model,
        None,
        load_rules,
        lambda: datetime.now(UTC),
        limit=50,
        load_rework=load_rework_rules,
    )


async def _ready(client: httpx.AsyncClient, csrf: dict[str, str], business: uuid.UUID) -> str:
    saved = await client.put(f"/api/businesses/{business}/profile", json=PROFILE, headers=csrf)
    assert saved.status_code == 200, saved.text
    return f"/api/businesses/{business}"


async def test_wish_with_fact_outside_profile_is_refused_before_model(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
) -> None:
    client, csrf, business = anna
    base = await _ready(client, csrf, business)
    refused = await client.post(
        f"{base}/ads/anna-direct/11/rework", json={"wish": "Скидка 30% новым"}, headers=csrf
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert "«30»" in detail and "в профиль" in detail
    async with sessions() as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM creative_sets WHERE business_id = :b AND rework IS NOT NULL"
            ),
            {"b": business},
        )
    assert count == 0


async def test_rework_writes_variants_and_owner_chooses_one(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
) -> None:
    client, csrf, business = anna
    base = await _ready(client, csrf, business)
    assert (
        await client.post(f"{base}/ads/anna-direct/11/rework", json={"wish": "Короче"})
    ).status_code == 403
    asked = await client.post(
        f"{base}/ads/anna-direct/11/rework", json={"wish": "Короче"}, headers=csrf
    )
    assert asked.status_code == 202, asked.text
    set_id = asked.json()["id"]
    assert (await client.get(f"{base}/reworks/{set_id}")).json()["status"] == "queued"

    # Сохранение профиля заказало и пополнение пустого направления: оно пишется первым.
    refill = ads({"headline": "Маникюр рядом с вами", "text": "Стерильные инструменты."})
    model = FakeModel(
        refill, ads(SHORT, {"headline": "Маникюр за 900 ₽", "text": "Только у нас."}, PLACE)
    )
    await _write(sessions, model)
    # Старый текст и просьба ушли модели строками JSON.
    assert '"Маникюр 1500 ₽"' in model.calls[1][1] and '"Короче"' in model.calls[1][1]
    view = (await client.get(f"{base}/reworks/{set_id}")).json()
    assert view["status"] == "ready" and view["ad_id"] == "11"
    assert [v["headline"] for v in view["variants"]] == [SHORT["headline"], PLACE["headline"]]

    # Невыбранные варианты не попадают в общий список и хранитель их не ставит.
    board = (await client.get(f"{base}/creatives")).json()["creatives"]
    assert [c for c in board if c["reason"] == "rework"] == []

    first, second = (v["id"] for v in view["variants"])
    chosen = await client.post(
        f"{base}/reworks/{set_id}/choose", json={"creative_id": second}, headers=csrf
    )
    assert chosen.status_code == 200, chosen.text
    assert "старое объявление работает" in chosen.json()["text"]
    states = {v["id"]: v["state"] for v in chosen.json()["variants"]}
    assert states == {first: "withdrawn", second: "launch_pending"}
    again = await client.post(
        f"{base}/reworks/{set_id}/choose", json={"creative_id": first}, headers=csrf
    )
    assert again.status_code == 409
    board = (await client.get(f"{base}/creatives")).json()["creatives"]
    chosen_rows = [(c["headline"], c["state"]) for c in board if c["reason"] == "rework"]
    assert chosen_rows == [(PLACE["headline"], "launch_pending")]
    async with sessions() as session:
        replaces = await session.scalar(
            text("SELECT replaces FROM creatives WHERE id = :i"), {"i": second}
        )
    assert replaces == "11"


async def test_without_model_says_so(anna: Anna, sessions: Sessions) -> None:  # noqa: F811
    client, csrf, business = anna
    base = await _ready(client, csrf, business)
    asked = await client.post(
        f"{base}/ads/anna-direct/11/rework", json={"wish": "Упор на адрес"}, headers=csrf
    )
    await _write(sessions, None)
    view = (await client.get(f"{base}/reworks/{asked.json()['id']}")).json()
    assert view["status"] == "failed" and view["variants"] == []
    assert "не ответил" in view["problem"]


async def test_unknown_ad_and_other_owner(
    anna: Anna,  # noqa: F811
    browser: Browser,
    provider: FakeProvider,
) -> None:
    client, csrf, business = anna
    base = await _ready(client, csrf, business)
    missing = await client.post(
        f"{base}/ads/anna-direct/999/rework", json={"wish": "Короче"}, headers=csrf
    )
    assert missing.status_code == 404
    asked = await client.post(
        f"{base}/ads/anna-direct/11/rework", json={"wish": "Короче"}, headers=csrf
    )
    other = browser()
    other_csrf = await _login(other, provider, BORIS)
    assert (await other.get(f"{base}/reworks/{asked.json()['id']}")).status_code == 404
    post = await other.post(
        f"{base}/ads/anna-direct/11/rework",
        json={"wish": "Короче"},
        headers={"X-CSRF-Token": other_csrf},
    )
    assert post.status_code == 404
