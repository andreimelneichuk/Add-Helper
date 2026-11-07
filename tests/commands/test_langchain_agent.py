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
    assert build_langchain_agent("https://api.openai.com/v1", SecretStr(""), "gpt-4o") is None


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
        return_value=_msg(
            {
                "reply": (
                    "Здравствуйте! Я ваш рекламный ассистент по Яндекс Директу. Чем могу помочь?"
                ),
                "action": None,
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((KnownAd("anna/1", "Стрижка"),), ("Стрижка", "Окрашивание"))
    analytics = AnalyticsContext("За 7 дней", 0, 0, 0, 0)

    result = await agent.invoke("Привет!", known, analytics)
    assert result is not None
    assert result.command.kind is Kind.UNKNOWN
    assert "Здравствуйте!" in result.reply


@pytest.mark.asyncio
async def test_agent_action_pause() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg(
            {
                "reply": "Приостанавливаю показы всех объявлений.",
                "action": "pause",
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((KnownAd("anna/1", "Стрижка"),), ("Стрижка",))

    result = await agent.invoke("Поставь на паузу", known)
    assert result is not None
    assert result.command.kind is Kind.PAUSE
    assert "Приостанавливаю" in result.reply


@pytest.mark.asyncio
async def test_agent_action_set_budget() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg(
            {
                "reply": "Меняю месячный бюджет на 40 000 рублей.",
                "action": "set_budget",
                "rubles": 40000,
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((), ())

    result = await agent.invoke("Сделай бюджет 40к", known)
    assert result is not None
    assert result.command.kind is Kind.SET_BUDGET
    assert result.command.rubles == 40000


@pytest.mark.asyncio
async def test_agent_action_set_price() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg(
            {
                "reply": "Обновляю цену на стрижку.",
                "action": "set_price",
                "service": "стрижка",
                "rubles": 2500,
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((), ("Стрижка", "Маникюр"))

    result = await agent.invoke("стрижка теперь 2500", known)
    assert result is not None
    assert result.command.kind is Kind.SET_PRICE
    assert result.command.service == "Стрижка"
    assert result.command.rubles == 2500


@pytest.mark.asyncio
async def test_agent_exact_ad_id_matching() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg(
            {
                "reply": "Отключаю объявление 1",
                "action": "ad_off",
                "ad_id": "1",
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((KnownAd("12", "Длинное"), KnownAd("1", "Короткое")), ())

    result = await agent.invoke("выключи 1", known)
    assert result is not None
    assert result.command.kind is Kind.AD_OFF
    assert result.command.ad is not None
    assert result.command.ad.id == "1"


@pytest.mark.asyncio
async def test_agent_substring_ad_id_does_not_match() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg(
            {
                "reply": "Отключаю объявление 1",
                "action": "ad_off",
                "ad_id": "1",
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((KnownAd("12", "Длинное"),), ())

    result = await agent.invoke("выключи 1", known)
    assert result is not None
    assert result.command.kind is Kind.UNKNOWN


@pytest.mark.asyncio
async def test_agent_hallucinated_rubles_rejected() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg(
            {
                "reply": "Увеличиваю бюджет до 50000",
                "action": "set_budget",
                "rubles": 50000,
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((), ())

    # В сообщении нет числа 50000!
    result = await agent.invoke("подними бюджет", known)
    assert result is not None
    assert result.command.kind is Kind.UNKNOWN


@pytest.mark.asyncio
async def test_agent_failure_returns_none() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("API timeout"))

    agent = LangChainChatAgent(mock_llm)
    known = Known((), ())

    result = await agent.invoke("привет", known)
    assert result is None


@pytest.mark.asyncio
async def test_agent_stats_uses_code_numbers() -> None:
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=_msg(
            {
                "reply": "Вы потратили 999 999 рублей и получили 100500 кликов!",
                "action": "stats",
            }
        )
    )

    agent = LangChainChatAgent(mock_llm)
    known = Known((), ())
    analytics = AnalyticsContext(
        period_label="За 7 дней",
        cost_micros=5_000_000_000,  # 5000 руб
        impressions=1000,
        clicks=42,
        conversions=3,
    )

    result = await agent.invoke("покажи статистику", known, analytics)
    assert result is not None
    assert result.command.kind is Kind.STATS
    # Цифры должны быть детерминированными из кода, а не выдуманными моделью
    assert "42" in result.reply
    assert "999 999" not in result.reply
