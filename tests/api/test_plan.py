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
