"""План бюджета через API: пределы открыты до входа, сумму сохраняет только владелец."""

import httpx

from adhelper.budget.adapters.plan_rules import load_plan_rules
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import BORIS, Browser, _login
from tests.api.test_site import Anna, anna  # noqa: F401

RULES = load_plan_rules()


async def test_options_are_open_before_login(browser: Browser) -> None:
    client = browser()
    options = (await client.get("/api/budget-plan/options")).json()
    assert options == {
        "min": RULES.min_monthly_rubles,
        "max": RULES.max_monthly_rubles,
        "presets": list(RULES.presets),
    }


async def test_owner_saves_plan(anna: Anna) -> None:  # noqa: F811
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}/budget-plan"
    headers = {"X-CSRF-Token": csrf}
    assert (await client.get(base)).json() == {"rubles": None}
    low = await client.put(base, json={"rubles": RULES.min_monthly_rubles - 1}, headers=headers)
    assert low.status_code == 422
    assert "не меньше" in low.json()["detail"]
    high = await client.put(base, json={"rubles": RULES.max_monthly_rubles + 1}, headers=headers)
    assert high.status_code == 422
    saved = await client.put(base, json={"rubles": 20_000}, headers=headers)
    assert saved.json() == {"rubles": 20_000}
    assert (await client.get(base)).json() == {"rubles": 20_000}
    # Без CSRF-ключа план не меняется.
    assert (await client.put(base, json={"rubles": 30_000})).status_code == 403


async def test_other_owner_cannot_touch_plan(
    anna: Anna,  # noqa: F811
    browser: Browser,
    provider: FakeProvider,
) -> None:
    _, _, business_id = anna
    other = browser()
    csrf = await _login(other, provider, BORIS)
    base = f"/api/businesses/{business_id}/budget-plan"
    assert (await other.get(base)).status_code == 404
    response = await other.put(base, json={"rubles": 20_000}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 404


async def test_plan_rejects_non_integer(anna: Anna) -> None:  # noqa: F811
    client, csrf, business_id = anna
    response = await client.put(
        f"/api/businesses/{business_id}/budget-plan",
        json={"rubles": 20000.5},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert isinstance(response, httpx.Response)


async def _second_business(client: httpx.AsyncClient, csrf: str, name: str) -> str:
    response = await client.post(
        "/api/businesses", json={"name": name}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_portfolio_default_is_separate(anna: Anna) -> None:  # noqa: F811
    client, _, _ = anna
    assert (await client.get("/api/me/budget-portfolio")).json() == {
        "mode": "separate",
        "rubles": None,
        "businesses": 1,
    }


async def test_shared_budget_is_split_over_all_sites_and_stored_on_the_server(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, first = anna
    second = await _second_business(client, csrf, "Второй сайт")
    headers = {"X-CSRF-Token": csrf}
    saved = await client.put(
        "/api/me/budget-portfolio", json={"mode": "shared", "rubles": 40_001}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    shares = {line["business_id"]: line["rubles"] for line in saved.json()["shares"]}
    assert sum(shares.values()) == 40_001
    assert set(shares) == {str(first), second}
    for business_id, rubles in shares.items():
        plan = await client.get(f"/api/businesses/{business_id}/budget-plan")
        assert plan.json() == {"rubles": rubles}
    got = (await client.get("/api/me/budget-portfolio")).json()
    assert got == {"mode": "shared", "rubles": 40_001, "businesses": 2}


async def test_shared_budget_below_minimum_per_site_changes_nothing(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, first = anna
    await _second_business(client, csrf, "Второй сайт")
    headers = {"X-CSRF-Token": csrf}
    await client.put(
        f"/api/businesses/{first}/budget-plan", json={"rubles": 20_000}, headers=headers
    )
    too_small = 2 * RULES.min_monthly_rubles - 1
    response = await client.put(
        "/api/me/budget-portfolio", json={"mode": "shared", "rubles": too_small}, headers=headers
    )
    assert response.status_code == 422
    assert "на каждый сайт" in response.json()["detail"]
    assert (await client.get(f"/api/businesses/{first}/budget-plan")).json() == {"rubles": 20_000}
    assert (await client.get("/api/me/budget-portfolio")).json()["mode"] == "separate"


async def test_switching_back_to_separate_keeps_plans(anna: Anna) -> None:  # noqa: F811
    client, csrf, first = anna
    headers = {"X-CSRF-Token": csrf}
    await client.put(
        "/api/me/budget-portfolio", json={"mode": "shared", "rubles": 30_000}, headers=headers
    )
    back = await client.put("/api/me/budget-portfolio", json={"mode": "separate"}, headers=headers)
    assert back.status_code == 200
    assert (await client.get("/api/me/budget-portfolio")).json()["mode"] == "separate"
    assert (await client.get(f"/api/businesses/{first}/budget-plan")).json() == {"rubles": 30_000}


async def test_portfolio_needs_csrf_and_login(anna: Anna, browser: Browser) -> None:  # noqa: F811
    client, _, _ = anna
    body = {"mode": "shared", "rubles": 30_000}
    assert (await client.put("/api/me/budget-portfolio", json=body)).status_code == 403
    assert (await browser().get("/api/me/budget-portfolio")).status_code == 401
