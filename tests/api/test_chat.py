"""Строка чата отвечает, как поняла просьбу, и ничего не меняет сама."""

import json

import httpx

from tests.api.test_control import Anna, Sessions, _app, _rows, anna, platform  # noqa: F401
from tests.commands.test_service import FakeModel

PROFILE = {
    "niche": "маникюр",
    "address": "ул. Ленина, 5",
    "services": [{"name": "Маникюр", "price": 1500}, {"name": "Педикюр", "price": 2000}],
    "features": [],
    "website": "",
}


async def _say(
    client: httpx.AsyncClient, csrf: dict[str, str], business: object, text: str
) -> dict:  # type: ignore[type-arg]
    reply = await client.post(
        f"/api/businesses/{business}/chat/command", json={"text": text}, headers=csrf
    )
    assert reply.status_code == 200, reply.text
    return reply.json()  # type: ignore[no-any-return]


async def test_rules_understand_without_changing_anything(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
) -> None:
    client, csrf, business = anna
    await client.put(f"/api/businesses/{business}/profile", json=PROFILE, headers=csrf)

    days = await _say(client, csrf, business, "Не показывай рекламу по воскресеньям")
    assert days["kind"] == "skip_days" and days["days"] == [7]
    assert days["confirm"].endswith("Применить?")
    assert days["source"] == "rules" and days["rules"] == "commands-1"

    price = await _say(client, csrf, business, "педикюр теперь 2200")
    assert (price["kind"], price["service"], price["rubles"]) == ("set_price", "Педикюр", 2200)

    ad = await _say(client, csrf, business, "отключи объявление «Маникюр рядом»")
    assert ad["kind"] == "ad_off" and ad["ad"] == {"id": "anna-direct/12", "name": "Маникюр рядом"}

    refusal = await _say(client, csrf, business, "сделай скидку 30%")
    assert refusal["kind"] == "unknown" and "профил" in refusal["reply"]
    assert await _rows(sessions, business) == []


async def test_model_is_asked_only_when_rules_do_not_know(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    model = FakeModel([json.dumps({"command": "ad_off", "ad": 0})])
    _app(client).state.command_model = model
    await _say(client, csrf, business, "пауза")
    assert model.seen == []
    answer = await _say(client, csrf, business, "убери то, что за полторы тысячи")
    assert answer["kind"] == "ad_off" and answer["source"] == "model"
    assert answer["ad"]["name"] == "Маникюр 1500 ₽"


async def test_model_calls_are_limited_per_day(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    model = FakeModel([json.dumps({"command": "stats"})] * 60)
    _app(client).state.command_model = model
    for _ in range(51):
        await _say(client, csrf, business, "ну что там")
    assert len(model.seen) == 50


async def test_other_business_is_not_reachable(anna: Anna) -> None:  # noqa: F811
    client, csrf, _ = anna
    other = "00000000-0000-0000-0000-000000000000"
    reply = await client.post(
        f"/api/businesses/{other}/chat/command", json={"text": "пауза"}, headers=csrf
    )
    assert reply.status_code == 404
