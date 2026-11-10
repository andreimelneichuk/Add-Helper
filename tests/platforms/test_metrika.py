"""Клиент Метрики: цели для обращений и визиты с рекламы. docs/03-integrations.md §3."""

from datetime import date

import httpx
import pytest

from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.platforms.adapters.metrika import MetrikaClient, lead_goal_ids


async def _token() -> str:
    return "t"


def _client(handler: httpx.MockTransport) -> MetrikaClient:
    return MetrikaClient(_token, "https://api-metrika.example", transport=handler)


GOALS = {
    "goals": [
        {"id": 1, "name": "Звонок", "type": "phone"},
        {"id": 2, "name": "Заявка", "type": "form"},
        {"id": 3, "name": "Мессенджер", "type": "messenger"},
        {"id": 4, "name": "Просмотр страницы цен", "type": "url"},
        {"id": 5, "name": "Глубина 3", "type": "number"},
        {"id": 6, "name": "Прокрутка", "type": "action"},
    ]
}


def test_only_lead_goals_count_as_leads() -> None:
    assert lead_goal_ids(GOALS["goals"]) == [1, 2, 3]
    assert lead_goal_ids([]) == []


async def test_goals_are_read_from_the_counter_with_oauth_header() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=GOALS)

    goals = await _client(httpx.MockTransport(handle)).lead_goals("98765432")

    assert goals == [1, 2, 3]
    assert seen[0].url.path == "/management/v1/counter/98765432/goals"
    assert seen[0].headers["authorization"] == "OAuth t"


async def test_ad_visits_sum_the_ad_traffic_for_the_moscow_days() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"totals": [[412.0]], "data": []})

    visits = await _client(httpx.MockTransport(handle)).ad_visits(
        "98765432", date(2026, 11, 14), date(2026, 11, 20)
    )

    assert visits == 412
    query = seen[0].url.params
    assert query["ids"] == "98765432"
    assert query["metrics"] == "ym:s:visits"
    assert (query["date1"], query["date2"]) == ("2026-11-14", "2026-11-20")
    assert "ad" in query["filters"]


async def test_no_data_is_unknown_not_zero() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totals": [], "data": []})

    assert (
        await _client(httpx.MockTransport(handle)).ad_visits(
            "1", date(2026, 11, 14), date(2026, 11, 20)
        )
        is None
    )


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_errors_mean_unavailable(status: int) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "x"})

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(PlatformUnavailable):
        await client.lead_goals("1")
    with pytest.raises(PlatformUnavailable):
        await client.ad_visits("1", date(2026, 11, 14), date(2026, 11, 20))
