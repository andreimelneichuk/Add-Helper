import json
from typing import Any

import pytest

from adhelper.commands.domain.router import (
    CLARIFICATION_AMBIGUOUS,
    Intent,
    classify_intent,
    match_layer_1,
)
from adhelper.creatives.domain.model import ModelReply


class FakeModel:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        return ModelReply(json.dumps(self.reply), 10)


def test_router_layer_1_mutations() -> None:
    res = match_layer_1("поставь на паузу")
    assert res is not None
    assert res.intent is Intent.MUTATION
    assert res.layer == 1
    assert res.confidence == 1.0

    res2 = match_layer_1("бюджет 20000")
    assert res2 is not None
    assert res2.intent is Intent.MUTATION

    res3 = match_layer_1("не показывай в субботу и воскресенье")
    assert res3 is not None
    assert res3.intent is Intent.MUTATION


def test_router_layer_1_information() -> None:
    res = match_layer_1("как дела с рекламой?")
    assert res is not None
    assert res.intent is Intent.INFORMATION
    assert res.layer == 1

    res2 = match_layer_1("что ты делал за последние 3 дня?")
    assert res2 is not None
    assert res2.intent is Intent.INFORMATION

    res3 = match_layer_1("сколько потратили за неделю?")
    assert res3 is not None
    assert res3.intent is Intent.INFORMATION


@pytest.mark.asyncio
async def test_router_layer_2_high_confidence() -> None:
    model = FakeModel({"intent": "mutation", "confidence": 0.95, "reason": "Запрос на паузу"})
    res = await classify_intent("давай притормозим пока показы", model)
    assert res.intent is Intent.MUTATION
    assert res.layer == 2
    assert res.confidence == 0.95


@pytest.mark.asyncio
async def test_router_layer_3_fallback_low_confidence() -> None:
    # Запрос не попадает в регулярки слоя 1, у модели низкая уверенность (< 0.85) -> Слой 3
    model = FakeModel(
        {"intent": "mutation", "confidence": 0.65, "reason": "Непонятно, вопрос или изменение"}
    )
    res = await classify_intent("что посоветуешь предпринять по рекламе?", model)
    assert res.intent is Intent.UNKNOWN
    assert res.layer == 3
    assert res.clarification == CLARIFICATION_AMBIGUOUS
