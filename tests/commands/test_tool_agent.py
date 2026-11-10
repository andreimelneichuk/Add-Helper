"""Агент с инструментами на подставной модели: цикл вызовов, проверка параметров, история."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic import SecretStr

from adhelper.commands.application.analytics import AdStatSummary, AnalyticsContext
from adhelper.commands.application.tool_agent import (
    ChatTurn,
    ToolChatAgent,
    build_tool_agent,
    stats_payload,
)
from adhelper.commands.domain.model import Kind, Known, KnownAd

KNOWN = Known((KnownAd("a/1", "Стрижка у метро"),), ("Стрижка",))
CTX = AnalyticsContext(
    period_label="За 7 дней",
    cost_micros=0,
    impressions=0,
    clicks=0,
    conversions=0,
    budget_rubles=15_000,
    spent_rubles=4_200,
)


def _reply(text: str = "", calls: list[tuple[str, dict[str, Any]]] | None = None) -> MagicMock:
    message = MagicMock()
    message.content = text
    message.tool_calls = [
        {"name": name, "args": args, "id": f"c{n}"} for n, (name, args) in enumerate(calls or [])
    ]
    return message


def _agent(*replies: MagicMock) -> tuple[ToolChatAgent, MagicMock]:
    queue = list(replies)
    bound = MagicMock()
    bound.seen = []

    async def answer(messages: list[Any]) -> MagicMock:
        bound.seen.append(list(messages))  # агент дополняет список после вызова: снимок
        return queue.pop(0)

    bound.ainvoke = answer
    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=bound)
    return ToolChatAgent(llm), bound


def test_agent_needs_a_key() -> None:
    assert build_tool_agent("https://x/v1", None, "m") is None
    assert build_tool_agent("https://x/v1", SecretStr(""), "m") is None
    assert build_tool_agent("https://x/v1", SecretStr("k"), "m") is not None


async def test_read_tool_result_goes_back_to_the_model() -> None:
    agent, bound = _agent(
        _reply(calls=[("get_budget", {})]), _reply("Потрачено 4 200 ₽ из 15 000 ₽.")
    )
    turn = await agent.invoke("сколько осталось", KNOWN, CTX)
    assert turn is not None and turn.reply.startswith("Потрачено")
    tool_message = bound.seen[1][-1]
    assert json.loads(tool_message.content)["left_month_rub"] == 10_800
    assert "10800" in turn.facts


async def test_stats_for_a_period_are_loaded_by_code() -> None:
    seen: list[str] = []

    async def loader(period: str) -> tuple[str, tuple[AdStatSummary, ...]]:
        seen.append(period)
        return "Вчера", (AdStatSummary("a/1", "Стрижка у метро", 1000, 40, 3, 600_000_000, 4.0),)

    agent, bound = _agent(
        _reply(calls=[("get_stats", {"period": "today"}), ("get_stats", {"period": "yesterday"})]),
        _reply("Готово."),
    )
    turn = await agent.invoke("цифры за сегодня и вчера", KNOWN, CTX, stats_loader=loader)
    assert seen == ["today", "yesterday"] and turn is not None
    payload = json.loads(bound.seen[1][-1].content)
    assert payload["totals"]["cost_per_lead_rub"] == 200


def test_empty_period_says_there_is_no_data() -> None:
    assert "данных нет" in str(stats_payload("Сегодня", ())["note"])


async def test_unknown_period_is_refused_by_the_tool() -> None:
    agent, bound = _agent(_reply(calls=[("get_stats", {"period": "год"})]), _reply("Не вышло."))
    await agent.invoke("за год", KNOWN, CTX)
    assert "error" in json.loads(bound.seen[1][-1].content)


async def test_proposal_is_recorded_only_when_the_owner_named_the_amount() -> None:
    agent, _ = _agent(
        _reply(calls=[("propose_set_budget", {"rubles": 20000})]), _reply("Предлагаю 20 000 ₽.")
    )
    turn = await agent.invoke("поставь бюджет 20000", KNOWN, CTX)
    assert turn is not None and turn.proposals[0].kind is Kind.SET_BUDGET
    assert turn.proposals[0].rubles == 20_000

    agent, _ = _agent(
        _reply(calls=[("propose_set_budget", {"rubles": 99000})]), _reply("Какая сумма?")
    )
    turn = await agent.invoke("поставь бюджет побольше", KNOWN, CTX)
    assert turn is not None and turn.proposals == ()


async def test_amount_from_an_earlier_owner_message_counts() -> None:
    agent, _ = _agent(
        _reply(calls=[("propose_set_budget", {"rubles": 12000})]), _reply("Предлагаю.")
    )
    history = [ChatTurn("user", "хочу тратить 12000 в месяц"), ChatTurn("assistant", "Поставить?")]
    turn = await agent.invoke("да, поставь", KNOWN, CTX, history=history)
    assert turn is not None and turn.proposals[0].rubles == 12_000


async def test_ad_must_be_in_the_list_and_rework_keeps_the_wish() -> None:
    agent, _ = _agent(_reply(calls=[("propose_ad_off", {"ad": 7})]), _reply("Какое?"))
    turn = await agent.invoke("отключи объявление", KNOWN, CTX)
    assert turn is not None and turn.proposals == ()

    agent, _ = _agent(
        _reply(calls=[("propose_rework_ad", {"ad": 0, "wish": "покороче"})]), _reply("Ок.")
    )
    turn = await agent.invoke("перепиши покороче", KNOWN, CTX)
    assert turn is not None and turn.proposals[0].wish == "покороче"
    assert turn.proposals[0].ad == KNOWN.ads[0]


async def test_read_only_agent_has_no_change_tools() -> None:
    agent, _ = _agent(_reply("Расскажу."))
    await agent.invoke("что умеешь", KNOWN, CTX, allow_changes=False)
    llm: Any = agent._llm
    names = {tool.name for tool in llm.bind_tools.call_args.args[0]}
    assert "get_stats" in names and not any(name.startswith("propose_") for name in names)


async def test_history_reaches_the_model_in_order() -> None:
    agent, bound = _agent(_reply("Ответ."))
    history = [ChatTurn("user", "Привет"), ChatTurn("assistant", "Здравствуйте")]
    await agent.invoke("а теперь?", KNOWN, CTX, history=history)
    sent = bound.seen[0]
    assert [m.content for m in sent[1:]] == ["Привет", "Здравствуйте", "а теперь?"]


async def test_model_failure_gives_none() -> None:
    bound = MagicMock()
    bound.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=bound)
    assert await ToolChatAgent(llm).invoke("привет", KNOWN, CTX) is None


async def test_confirmation_wording_without_a_proposal_is_sent_back_to_the_model() -> None:
    agent, bound = _agent(
        _reply("Предлагаю отключить объявление. Применить?"),
        _reply(calls=[("propose_ad_off", {"ad": 0})]),
        _reply("Предлагаю отключить."),
    )
    history = [ChatTurn("user", "про Стрижку у метро")]
    turn = await agent.invoke("отключи его", KNOWN, CTX, history=history)
    assert turn is not None and turn.proposals[0].kind is Kind.AD_OFF
    assert len(bound.seen) == 3
