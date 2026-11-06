import json
from typing import Any

import pytest

from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.application.mutation import MutationAgent, build_proposal_confirmation
from adhelper.commands.domain.model import Kind, Known, KnownAd, Source
from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

RULES = load_rules("commands-2")
ADS = (
    KnownAd("acc/1", "Маникюр со скидкой 20%"),
    KnownAd("acc/2", "Педикюр классический"),
)
KNOWN = Known(ADS, ("Маникюр", "Педикюр"))


class FakeModel:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.replies:
            raise ModelUnavailable("No reply")
        return ModelReply(self.replies.pop(0), 10)


@pytest.mark.asyncio
async def test_mutation_agent_isolates_user_message_and_parses_budget() -> None:
    model = FakeModel([json.dumps({"command": "set_budget", "rubles": 25000})])
    agent = MutationAgent(model)

    parsed = await agent.propose("увеличь бюджет до 25000 рублей", KNOWN, RULES)

    assert parsed.command.kind is Kind.SET_BUDGET
    assert parsed.command.rubles == 25000
    assert parsed.source is Source.MODEL

    # Проверяем, что сообщение изолировано в тегах данных
    call = model.calls[0]
    payload = json.loads(call["user"])
    assert payload["raw_message"] == "<user_message>увеличь бюджет до 25000 рублей</user_message>"
    assert len(payload["ads"]) == 2

    # Проверяем текст предложения
    confirm = build_proposal_confirmation(parsed.command)
    assert "бюджет 25 000 ₽ в месяц" in confirm
    assert confirm.endswith("Применить?")


@pytest.mark.asyncio
async def test_mutation_agent_prevents_hallucinated_numbers() -> None:
    # Модель попыталась вернуть 50000, которого нет в сообщении пользователя
    model = FakeModel([json.dumps({"command": "set_budget", "rubles": 50000})])
    agent = MutationAgent(model)

    parsed = await agent.propose("увеличь бюджет немного", KNOWN, RULES)

    assert parsed.command.kind is Kind.UNKNOWN


@pytest.mark.asyncio
async def test_mutation_agent_ad_off_validation() -> None:
    model = FakeModel([json.dumps({"command": "ad_off", "ad": 1})])
    agent = MutationAgent(model)

    parsed = await agent.propose("выключи педикюр", KNOWN, RULES)

    assert parsed.command.kind is Kind.AD_OFF
    assert parsed.command.ad == ADS[1]
    confirm = build_proposal_confirmation(parsed.command)
    assert "«Педикюр классический»" in confirm


@pytest.mark.asyncio
async def test_mutation_agent_handles_model_unavailable() -> None:
    model = FakeModel([])
    agent = MutationAgent(model)

    parsed = await agent.propose("пауза", KNOWN, RULES)
    assert parsed.command.kind is Kind.UNKNOWN
