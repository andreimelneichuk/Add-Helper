import json
from dataclasses import dataclass, field

from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.application.service import CallBudget, interpret
from adhelper.commands.domain.model import Kind, Known, KnownAd
from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

KNOWN = Known((KnownAd("acc/1", "Маникюр у метро"),), ("Маникюр",))
RULES = load_rules("commands-1")


@dataclass
class FakeModel:
    replies: list[str]
    seen: list[str] = field(default_factory=list)

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.seen.append(user)
        if not self.replies:
            raise ModelUnavailable("down")
        return ModelReply(self.replies.pop(0), 10)


def always() -> bool:
    return True


async def test_rules_go_first_and_confirm_is_built_by_code() -> None:
    model = FakeModel([])
    answer = await interpret("не показывай по воскресеньям", KNOWN, RULES, model, always)
    assert answer.command.kind is Kind.SKIP_DAYS
    assert answer.confirm == (
        "Понял так: не показывать рекламу: воскресенье. Бюджет месяца не изменится. Применить?"
    )
    assert answer.source == "rules"
    assert model.seen == []


async def test_model_answer_is_checked() -> None:
    model = FakeModel([json.dumps({"command": "ad_off", "ad": 0})])
    answer = await interpret("убери ту, что про руки", KNOWN, RULES, model, always)
    assert answer.command.kind is Kind.AD_OFF
    assert answer.source == "model"
    assert "«Маникюр у метро»" in answer.confirm
    # Модель видит сообщение и названия как данные, без бюджета.
    assert json.loads(model.seen[0]) == {
        "message": "убери ту, что про руки",
        "ads": [{"n": 0, "name": "Маникюр у метро"}],
        "services": ["Маникюр"],
    }


async def test_model_failure_is_an_honest_refusal() -> None:
    answer = await interpret("сделай скидку 30%", KNOWN, RULES, FakeModel([]), always)
    assert answer.command.kind is Kind.UNKNOWN
    assert "Скидки" in answer.reply
    broken = await interpret("что-то странное", KNOWN, RULES, FakeModel(["[1]"]), always)
    assert broken.command.kind is Kind.UNKNOWN
    assert "ничего не изменил" in broken.reply


async def test_no_model_call_past_the_limit() -> None:
    model = FakeModel([json.dumps({"command": "pause"})])
    answer = await interpret("тормозни всё", KNOWN, RULES, model, lambda: False)
    assert answer.command.kind is Kind.UNKNOWN
    assert model.seen == []


def test_call_budget_counts_per_business_and_day() -> None:
    budget = CallBudget()
    assert budget.take("b1", "2026-11-03", 1)
    assert not budget.take("b1", "2026-11-03", 1)
    assert budget.take("b2", "2026-11-03", 1)
    assert budget.take("b1", "2026-11-04", 1)


async def test_two_requests_get_two_confirmations() -> None:
    known = Known(
        (KnownAd("acc/1", "Маникюр у метро"), KnownAd("acc/2", "Педикюр у метро")),
        ("Маникюр", "Педикюр"),
    )
    model = FakeModel([])
    rules = load_rules("commands-2")
    answer = await interpret(
        "убавь бюджет до 10к и отключи рекламу с педикюром", known, rules, model, always
    )
    assert answer.command.kind is Kind.SET_BUDGET
    assert answer.confirm == "Понял так: бюджет 10\u00a0000\u00a0₽ в месяц. Применить?"
    assert [more.command.kind for more in answer.more] == [Kind.AD_OFF]
    assert "«Педикюр у метро»" in answer.more[0].confirm
    assert answer.rules == "commands-2"
    assert model.seen == []
