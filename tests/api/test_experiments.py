"""Предложение теста в «Сегодня»: согласие, отказ и отмена владельцем."""

import uuid

from adhelper.autopilot.adapters.sql import SqlDecisionLog
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import BORIS, Browser, _login
from tests.api.test_control import LOGIN, NOW, Anna, Sessions, _rows, anna, platform

__all__ = ["anna", "platform"]
RUB = 1_000_000
PROPOSED = "awaiting_confirmation"


async def offer(sessions: Sessions, business: uuid.UUID) -> int:
    inputs = {
        "spend_micros": 1200 * RUB,
        "leads": 3,
        "target_micros": 300 * RUB,
        "limit_micros": 800 * RUB,
        "p_good_bp": 3000,
        "p_bad_bp": 2000,
        "hypothesis": "price",
        "campaign_id": "1",
    }
    return await SqlDecisionLog(sessions, business).record(
        LOGIN,
        "ad:11",
        "borderline",
        "start_test",
        "awaiting_confirmation",
        inputs,
        "autopilot-2",
        NOW,
    )


async def kinds(anna: Anna) -> list[str]:
    client, _, business = anna
    cards = (await client.get(f"/api/businesses/{business}/today")).json()["cards"]
    return [card["kind"] for card in cards]


async def test_owner_agrees_then_cancels_and_platform_is_untouched(
    anna: Anna, sessions: Sessions
) -> None:
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    proposal = await offer(sessions, business)

    cards = (await client.get(f"{base}/today")).json()["cards"]
    card = next(c for c in cards if c["kind"] == "test_offer")
    assert card["ref"] == str(proposal)
    assert card["text"].startswith("«Маникюр 1500")

    url = f"{base}/tests/offers/{proposal}"
    answer = await client.post(url, json={"agree": True}, headers=csrf)
    assert answer.status_code == 200
    assert answer.json()["status"] == "prepared"
    assert "test_offer" not in await kinds(anna)
    cards = (await client.get(f"{base}/today")).json()["cards"]
    preparing = next(c for c in cards if c["kind"] == "test_preparing")

    again = await client.post(url, json={"agree": True}, headers=csrf)
    assert again.status_code == 409

    cancel = await client.post(f"{base}/tests/{preparing['ref']}/cancel", headers=csrf)
    assert cancel.status_code == 200
    assert "test_preparing" not in await kinds(anna)
    assert await _rows(sessions, business) == []


async def test_owner_declines_and_offer_disappears(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business = anna
    proposal = await offer(sessions, business)

    answer = await client.post(
        f"/api/businesses/{business}/tests/offers/{proposal}", json={"agree": False}, headers=csrf
    )

    assert answer.json()["status"] == "declined"
    kinds_now = await kinds(anna)
    assert "test_offer" not in kinds_now and "test_preparing" not in kinds_now


async def test_answer_needs_csrf_and_owner(
    anna: Anna, sessions: Sessions, browser: Browser, provider: FakeProvider
) -> None:
    client, _csrf, business = anna
    proposal = await offer(sessions, business)
    url = f"/api/businesses/{business}/tests/offers/{proposal}"
    assert (await client.post(url, json={"agree": True})).status_code == 403

    other = browser()
    other_csrf = await _login(other, provider, BORIS)
    refused = await other.post(url, json={"agree": True}, headers={"X-CSRF-Token": other_csrf})
    assert refused.status_code == 404
    assert "test_offer" in await kinds(anna)


async def test_unknown_test_is_not_found(anna: Anna) -> None:
    client, csrf, business = anna
    missing = await client.post(f"/api/businesses/{business}/tests/999/cancel", headers=csrf)
    assert missing.status_code == 404
