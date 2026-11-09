"""Настройки автопилота через HTTP: чтение, запись, остановка, чужой бизнес, запрет «Сам»."""

from sqlalchemy import text

from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import BORIS, Browser, _login
from tests.api.test_control import Anna, Sessions, _rows, anna, platform

__all__ = ["anna", "platform"]
URL = "/api/businesses/{}/autopilot"


async def test_defaults_are_read_without_a_stored_row(anna: Anna, sessions: Sessions) -> None:
    client, _, business = anna
    answer = await client.get(URL.format(business))
    assert answer.status_code == 200
    body = answer.json()
    assert body["mode"] == "confirm"
    assert body["allowed_actions"] == ["pause_ad", "start_test"]
    assert (body["cadence"], body["run_hour"]) == ("daily", 6)
    assert body["status"] == "no_goal"
    assert body["auto_allowed"] is False
    async with sessions() as session:
        count = await session.scalar(text("SELECT count(*) FROM autopilot_settings"))
    assert count == 0  # чтение ничего не создаёт


async def test_put_is_saved_and_written_to_history(anna: Anna) -> None:
    client, csrf, business = anna
    url = URL.format(business)
    done = await client.put(url, json={"mode": "watch"}, headers=csrf)
    assert done.status_code == 200
    again = (await client.get(url)).json()
    assert (again["mode"], again["run_hour"], again["cadence"]) == ("watch", 6, "daily")
    assert again["history"][0]["text"] == "Перевёл автопилот в режим «Только следит»"


async def test_unchanged_put_adds_nothing_to_history(anna: Anna) -> None:
    client, csrf, business = anna
    url = URL.format(business)
    await client.put(url, json={"run_hour": 7}, headers=csrf)
    await client.put(url, json={"run_hour": 7}, headers=csrf)
    history = (await client.get(url)).json()["history"]
    assert len(history) == 1


async def test_bad_values_are_refused(anna: Anna) -> None:
    client, csrf, business = anna
    url = URL.format(business)
    bad: tuple[dict[str, object], ...] = (
        {"run_hour": 24},
        {"mode": "robot"},
        {"cadence": "hourly"},
        {"allowed_actions": []},
        {"allowed_actions": ["delete_campaign"]},
        {"envelope": {"x": -1}},
    )
    for body in bad:
        assert (await client.put(url, json=body, headers=csrf)).status_code == 422


async def test_auto_is_refused_while_not_allowed(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business = anna
    answer = await client.put(URL.format(business), json={"mode": "auto"}, headers=csrf)
    assert answer.status_code == 409
    assert "ещё не разрешён" in answer.json()["detail"]
    assert (await client.get(URL.format(business))).json()["mode"] == "confirm"


async def test_auto_is_accepted_once_allowed(anna: Anna) -> None:
    client, csrf, business = anna
    client._transport.app.state.autopilot_auto_allowed = True  # type: ignore[attr-defined]
    answer = await client.put(URL.format(business), json={"mode": "auto"}, headers=csrf)
    assert answer.status_code == 200
    assert answer.json()["mode"] == "auto"


async def test_stop_and_resume(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business = anna
    url = URL.format(business)
    stopped = await client.post(f"{url}/stop", headers=csrf)
    assert stopped.status_code == 200
    body = (await client.get(url)).json()
    assert body["status"] == "stopped"
    assert body["next_run_at"] is None
    assert body["history"][0]["text"] == "Остановил автопилот"
    # Смена расписания остановку не снимает.
    await client.put(url, json={"run_hour": 9}, headers=csrf)
    assert (await client.get(url)).json()["status"] == "stopped"
    resumed = await client.put(url, json={"resume": True}, headers=csrf)
    assert resumed.status_code == 200
    assert (await client.get(url)).json()["status"] != "stopped"
    assert (await client.get(url)).json()["history"][0]["text"] == "Снова включил автопилот"


async def test_status_follows_goal_and_connection(anna: Anna, sessions: Sessions) -> None:
    client, _, business = anna
    async with sessions.begin() as session:
        await session.execute(
            text("INSERT INTO lead_goals (business_id, goal_micros) VALUES (:b, 90000000)"),
            {"b": business},
        )
    body = (await client.get(URL.format(business))).json()
    assert body["status"] == "not_connected"  # площадки для этого бизнеса в тесте нет
    client._transport.app.state.platform_for = lambda _b, _a: object()  # type: ignore[attr-defined]
    assert (await client.get(URL.format(business))).json()["status"] == "ok"
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO alerts (business_id, kind, details)"
                " VALUES (:b, 'data_untrusted', '{}')"
            ),
            {"b": business},
        )
    assert (await client.get(URL.format(business))).json()["status"] == "data_untrusted"


async def test_settings_changes_never_touch_the_platform(anna: Anna, sessions: Sessions) -> None:
    client, csrf, business = anna
    before = await _rows(sessions, business)
    await client.put(URL.format(business), json={"mode": "watch"}, headers=csrf)
    await client.post(f"{URL.format(business)}/stop", headers=csrf)
    assert await _rows(sessions, business) == before


async def test_other_users_business_is_not_found(
    anna: Anna, browser: Browser, provider: FakeProvider
) -> None:
    _, _, business = anna
    boris = browser()
    csrf = await _login(boris, provider, BORIS)
    headers = {"X-CSRF-Token": csrf}
    assert (await boris.get(URL.format(business))).status_code == 404
    assert (
        await boris.put(URL.format(business), json={"run_hour": 9}, headers=headers)
    ).status_code == 404
    assert (await boris.post(f"{URL.format(business)}/stop", headers=headers)).status_code == 404


async def test_report_is_empty_and_honest_without_actions(anna: Anna) -> None:
    client, _, business = anna
    answer = await client.get(URL.format(business) + "/report")
    assert answer.status_code == 200
    body = answer.json()
    assert (body["done"], body["better"], body["same"], body["worse"], body["rolled_back"]) == (
        0,
        0,
        0,
        0,
        0,
    )
    assert body["outcomes"] == []


async def test_report_of_another_business_is_not_readable(anna: Anna) -> None:
    client, _, _ = anna
    answer = await client.get(URL.format("00000000-0000-0000-0000-000000000001") + "/report")
    assert answer.status_code in {403, 404}
