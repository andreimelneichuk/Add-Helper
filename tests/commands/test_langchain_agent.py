"""Тесты для LangChain агента чата Ad Helper."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from adhelper.commands.application.analytics import AnalyticsContext
from adhelper.commands.application.langchain_agent import (
    LangChainChatAgent,
    build_langchain_agent,
)
from adhelper.commands.domain.model import Kind, Known, KnownAd


def test_build_langchain_agent_without_key() -> None:
    assert build_langchain_agent("https://api.openai.com/v1", None, "gpt-4o") is None
    assert (
        build_langchain_agent("https://api.openai.com/v1", SecretStr(""), "gpt-4o")
        is None
    )


def test_build_langchain_agent_with_key() -> None:
    agent = build_langchain_agent(
        "https://openrouter.ai/api/v1", SecretStr("sk-test"), "google/gemma-4-31b-it"
    )
    assert agent is not None
    assert isinstance(agent, LangChainChatAgent)


def _msg(data: dict) -> MagicMock:  # type: ignore[type-arg]
    m = MagicMock()
    m.content = json.dumps(data, ensure_ascii=False)
    return m


@pytest.mark.asyncio
async def test_agent_human_greeting_returns_unknown_with_human_reply() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg({
            "reply": "Здравствуйте! Я ваш рекламный ассистент по Яндекс Директу. Чем могу помочь?",
            "action": None,
        })
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((KnownAd("anna/1", "Стрижка"),), ("Стрижка", "Окрашивание"))
    analytics = AnalyticsContext("За 7 дней", 0, 0, 0, 0)

    result = await agent.invoke("Привет!", known, analytics)
    assert result.command.kind is Kind.UNKNOWN
    assert "Здравствуйте!" in result.reply


@pytest.mark.asyncio
async def test_agent_action_pause() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg({
            "reply": "Приостанавливаю показы всех объявлений.",
            "action": "pause",
        })
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((KnownAd("anna/1", "Стрижка"),), ("Стрижка",))

    result = await agent.invoke("Поставь на паузу", known)
    assert result.command.kind is Kind.PAUSE
    assert "Приостанавливаю" in result.reply


@pytest.mark.asyncio
async def test_agent_action_set_budget() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg({
            "reply": "Меняю месячный бюджет на 40 000 рублей.",
            "action": "set_budget",
            "rubles": 40000,
        })
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((), ())

    result = await agent.invoke("Сделай бюджет 40к", known)
    assert result.command.kind is Kind.SET_BUDGET
    assert result.command.rubles == 40000


@pytest.mark.asyncio
async def test_agent_action_set_price() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg({
            "reply": "Обновляю цену на стрижку.",
            "action": "set_price",
            "service": "стрижка",
            "rubles": 2500,
        })
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((), ("Стрижка", "Маникюр"))

    result = await agent.invoke("стрижка теперь 2500", known)
    assert result.command.kind is Kind.SET_PRICE
    assert result.command.service == "Стрижка"
    assert result.command.rubles == 2500
