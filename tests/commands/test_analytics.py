import json
from dataclasses import replace
from typing import Any

import pytest

from adhelper.commands.application.analytics import (
    AdStatSummary,
    AnalyticsAgent,
    AnalyticsContext,
)
from adhelper.commands.domain.model import Kind, Source
from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

CONTEXT = AnalyticsContext(
    period_label="За 7 дней",
    cost_micros=12_000_000_000,  # 12 000 руб
    impressions=2500,
    clicks=140,
    conversions=14,
    budget_rubles=20000,
    spent_rubles=12000,
    ads=(
        AdStatSummary(
            id="ad1",
            title="Маникюр с гарантией",
            impressions=1500,
            clicks=90,
            conversions=10,
            cost_micros=8_000_000_000,
            ctr_percent=6.0,
        ),
        AdStatSummary(
            id="ad2",
            title="Педикюр у метро",
            impressions=1000,
            clicks=50,
            conversions=4,
            cost_micros=4_000_000_000,
            ctr_percent=5.0,
        ),
    ),
    recent_history=(
        {
            "at": "2026-11-05T10:00:00",
            "explanation": "Снизил дневной лимит из-за перерасхода",
            "kind": "set_limit",
        },
        {
            "at": "2026-11-04T12:00:00",
            "explanation": "Отключил объявление «Педикюр дешево»",
            "kind": "pause_ad",
        },
    ),
)


class FakeModel:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.replies:
            raise ModelUnavailable("down")
        return ModelReply(self.replies.pop(0), 20)


@pytest.mark.asyncio
async def test_analytics_agent_answers_with_model() -> None:
    reply_payload = json.dumps(
        {
            "reply": (
                "За последние 7 дней все идет отлично: 14 обращений по средней цене около 857 ₽. "
                "Лидер — объявление с гарантией."
            ),
            "suggested_view": "stats",
        }
    )
    model = FakeModel([reply_payload])
    agent = AnalyticsAgent(model)

    answer = await agent.answer("Как дела с рекламой?", CONTEXT)

    assert answer.source is Source.MODEL
    assert answer.kind is Kind.STATS
    assert answer.suggested_view == "stats"
    assert "14 обращений" in answer.reply

    # Проверяем, что в запросе к модели переданы правильные факты
    user_data = json.loads(model.calls[0]["user"])
    assert user_data["question"] == "Как дела с рекламой?"
    assert user_data["analytics_context"]["leads"] == 14


@pytest.mark.asyncio
async def test_analytics_agent_fallback_stats() -> None:
    # Без модели fallback генерирует связный ответ на основе реальных данных
    agent = AnalyticsAgent(None)
    answer = await agent.answer("как дела?", CONTEXT)

    assert answer.source is Source.RULES
    assert answer.kind is Kind.STATS
    assert "12 000 ₽" in answer.reply
    assert "14 обращений" in answer.reply
    assert "Маникюр с гарантией" in answer.reply


@pytest.mark.asyncio
async def test_analytics_agent_fallback_history() -> None:
    agent = AnalyticsAgent(None)
    answer = await agent.answer("что ты делал?", CONTEXT, target_kind=Kind.HISTORY)

    assert answer.source is Source.RULES
    assert answer.kind is Kind.HISTORY
    assert answer.suggested_view == "history"
    assert "Снизил дневной лимит" in answer.reply
    assert "Отключил объявление" in answer.reply


@pytest.mark.asyncio
async def test_analytics_agent_fallback_best_ad_skips_stopped() -> None:
    # Лучшим не называем остановленное объявление, даже если у него больше обращений.
    stopped = AdStatSummary(
        id="ad0",
        title="Старое объявление",
        impressions=3000,
        clicks=200,
        conversions=30,
        cost_micros=9_000_000_000,
        ctr_percent=6.0,
        active=False,
    )
    context = replace(CONTEXT, ads=(stopped, *CONTEXT.ads))
    answer = await AnalyticsAgent(None).answer("как дела?", context)

    assert "Старое объявление" not in answer.reply
    assert "Маникюр с гарантией" in answer.reply
