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
    assert days["source"] == "rules" and days["rules"] == "commands-2" and days["next"] == []

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


async def test_chat_analytics_agent_answers_with_stats_context(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    answer = await _say(client, csrf, business, "как дела с рекламой?")
    assert answer["kind"] == "stats"
    assert "потрачено" in answer["reply"].lower() or "обращений" in answer["reply"].lower()


async def test_chat_proposal_card_for_budget_mutation(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    answer = await _say(client, csrf, business, "бюджет 25000")
    assert answer["kind"] == "set_budget"
    assert answer["rubles"] == 25000
    assert "25 000 ₽" in answer["confirm"] or "25 000" in answer["confirm"]
    assert answer["confirm"].endswith("Применить?")


async def test_chat_history_inquiry_answers_with_details(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    answer = await _say(client, csrf, business, "что ты делал?")
    assert answer["kind"] == "history"
    assert len(answer["reply"]) > 0


async def test_chat_tool_agent_handles_conversational_greeting(anna: Anna) -> None:  # noqa: F811
    from unittest.mock import AsyncMock, MagicMock

    from adhelper.commands.application.tool_agent import ToolChatAgent

    client, csrf, business = anna
    expected_reply = "Здравствуйте! Я ваш рекламный ассистент по Яндекс Директу. Чем могу помочь?"
    bound = MagicMock()
    reply = MagicMock()
    reply.content = expected_reply
    reply.tool_calls = []
    bound.ainvoke = AsyncMock(return_value=reply)
    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=bound)

    _app(client).state.tool_agent = ToolChatAgent(llm)
    answer = await _say(client, csrf, business, "Привет")
    assert answer["kind"] == "unknown"
    assert answer["reply"] == expected_reply
    assert answer["confirm"] == ""


async def test_chat_passes_the_recent_messages_to_the_agent(anna: Anna) -> None:  # noqa: F811
    from unittest.mock import AsyncMock

    from adhelper.commands.application.tool_agent import AgentTurn, ToolChatAgent

    client, csrf, business = anna
    agent = AsyncMock(spec=ToolChatAgent)
    agent.invoke.return_value = AgentTurn(reply="Тогда лучше снизить бюджет.")
    _app(client).state.tool_agent = agent
    history = [{"role": "user", "text": "Привет"}, {"role": "assistant", "text": "Здравствуйте"}]
    response = await client.post(
        f"/api/businesses/{business}/chat/command",
        json={"text": "Что посоветуешь?", "history": history},
        headers=csrf,
    )
    assert response.status_code == 200
    seen = agent.invoke.call_args.kwargs["history"]
    assert [(turn.role, turn.text) for turn in seen] == [
        ("user", "Привет"),
        ("assistant", "Здравствуйте"),
    ]


async def test_every_answer_tells_how_long_the_question_stays_valid(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, business = anna
    answer = await _say(client, csrf, business, "пауза")
    assert answer["expires_in"] == 600


async def test_chat_budget_is_the_number_owner_sees_not_the_working_part(
    anna: Anna,  # noqa: F811
) -> None:
    """В чате тот же бюджет, что в «Обзоре» и в настройках: лимит месяца без вычета резерва."""
    client, csrf, business = anna
    answer = await _say(client, csrf, business, "Какой у меня сейчас бюджет?")
    assert "Бюджет на месяц: 30 000 ₽" in answer["reply"].replace("\xa0", " ")


def test_ads_on_review_skip_copies_already_in_direct_and_archived() -> None:
    """Как на экране «Реклама»: копия, что уже есть в Директе, и архивная не считаются."""
    from types import SimpleNamespace as Item

    from adhelper.api.chat import awaiting_review

    def wiz(headline: str, text: str, status: str | None, archived: bool = False) -> Item:
        return Item(headline=headline, text=text, publication_status=status, archived=archived)

    items = [
        wiz("Укладка", "Текст  один", "submitted"),
        wiz("Бритьё", "Текст два", "submitted"),
        wiz("Архив", "Текст", "submitted", archived=True),
        wiz("Черновик", "Текст", None),
    ]
    direct = [
        Item(title="укладка", text="текст один", state="active"),
        Item(title="Королевское бритьё", text="Текст три", state="moderation"),
    ]
    # Объявления Директа в состоянии «moderation» идут первыми, затем отправленные из мастера.
    assert awaiting_review(items, direct) == ("Королевское бритьё", "Бритьё")  # type: ignore[arg-type]


async def test_autopilot_report_and_switch_through_chat(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, business = anna
    report = await _say(client, csrf, business, "что сделал автопилот")
    assert report["kind"] == "stats" and "Автопилот" in report["reply"]
    off = await _say(client, csrf, business, "выключи автопилот")
    assert off["kind"] == "autopilot_off" and off["confirm"].endswith("Применить?")
    weekly = await _say(client, csrf, business, "пусть автопилот работает раз в неделю")
    assert (weekly["kind"], weekly["cadence"]) == ("autopilot_cadence", "weekly")
