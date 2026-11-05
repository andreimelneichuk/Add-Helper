"""Правка цены в профиле доходит до работающих объявлений: замена заказана сразу."""

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text

from adhelper.creatives.adapters.rules_file import load_rework_rules, load_rules
from adhelper.creatives.adapters.sql import SqlCreativeQueue, SqlProfileSource
from adhelper.creatives.application.service import run_queued
from tests.api.test_control import Anna, Sessions, anna, platform  # noqa: F401
from tests.creatives.test_generate import FakeModel, ads

PROFILE = {
    "niche": "маникюр",
    "address": "ул. Ленина, 5",
    "services": [{"name": "Маникюр", "price": 1500}],
    "features": ["Стерильные инструменты"],
    "website": "",
}


async def _sets(sessions: Sessions, business: uuid.UUID) -> list[dict[str, Any]]:
    async with sessions() as session:
        rows = await session.execute(
            text("SELECT reason, rework FROM creative_sets WHERE business_id = :b ORDER BY id"),
            {"b": business},
        )
        return [dict(r._mapping) for r in rows]


async def _replacements(client: httpx.AsyncClient, business: uuid.UUID) -> list[dict[str, str]]:
    board = (await client.get(f"/api/businesses/{business}/creatives")).json()["creatives"]
    return [c for c in board if c["reason"] == "fact_changed"]


async def test_new_price_orders_replacement_for_live_ad(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
) -> None:
    client, csrf, business = anna
    url = f"/api/businesses/{business}/profile"
    # Объявление 11 «Маникюр 1500 ₽» совпадает с профилем: ничего не устарело.
    same = await client.put(url, json=PROFILE, headers=csrf)
    assert same.status_code == 200 and same.json()["outdated"] == []
    assert [s for s in await _sets(sessions, business) if s["rework"]] == []

    raised = {**PROFILE, "services": [{"name": "Маникюр", "price": 1700}]}
    saved = await client.put(url, json=raised, headers=csrf)
    assert saved.status_code == 200, saved.text
    outdated = saved.json()["outdated"]
    assert [(o["ad_id"], o["numbers"], o["replacing"]) for o in outdated] == [
        ("11", ["1500"], True)
    ]
    ordered = [s for s in await _sets(sessions, business) if s["rework"]]
    assert len(ordered) == 1 and ordered[0]["reason"] == "fact_changed"
    assert ordered[0]["rework"]["ad_id"] == "11" and ordered[0]["rework"]["auto"] is True

    # Повторное сохранение второй набор не заказывает.
    again = await client.put(url, json=raised, headers=csrf)
    assert again.json()["outdated"][0]["replacing"] is True
    assert len([s for s in await _sets(sessions, business) if s["rework"]]) == 1

    # Первый набор — обычное пополнение пустого направления, второй — замена.
    model = FakeModel(
        ads({"headline": "Маникюр на Ленина, 5", "text": "Стерильные инструменты."}),
        ads({"headline": "Маникюр 1700 ₽", "text": "Стерильные инструменты."}),
    )
    await run_queued(
        SqlCreativeQueue(sessions),
        SqlProfileSource(sessions),
        model,
        None,
        load_rules,
        lambda: datetime.now(UTC),
        load_rework=load_rework_rules,
    )
    board = await _replacements(client, business)
    # Без судьи текст модели запускает владелец: замена ждёт его решения.
    assert [(c["headline"], c["state"], c["reason"]) for c in board] == [
        ("Маникюр 1700 ₽", "needs_owner", "fact_changed")
    ]
    async with sessions() as session:
        replaces = await session.scalar(
            text("SELECT replaces FROM creatives WHERE business_id = :b AND reason = :r"),
            {"b": business, "r": "fact_changed"},
        )
    assert replaces == "11"

    # Цена изменилась снова: ждущая замена устарела, она снимается и заказывается новая.
    lower = {**PROFILE, "services": [{"name": "Маникюр", "price": 1600}]}
    await client.put(url, json=lower, headers=csrf)
    board = await _replacements(client, business)
    assert [(c["headline"], c["state"]) for c in board] == [("Маникюр 1700 ₽", "stopped")]
    assert len([s for s in await _sets(sessions, business) if s["rework"]]) == 2
