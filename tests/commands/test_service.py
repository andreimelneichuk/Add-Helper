import json
from dataclasses import dataclass, field

from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.application.service import CallBudget, interpret
from adhelper.commands.domain.model import Command, Kind, Known, KnownAd
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


async def test_free_reply_cannot_claim_an_action_or_invent_numbers() -> None:
    from unittest.mock import AsyncMock

    from adhelper.commands.application.langchain_agent import AgentResult, LangChainChatAgent

    agent = AsyncMock(spec=LangChainChatAgent)
    agent.invoke.return_value = AgentResult(
        Command(Kind.UNKNOWN), "Принял! Устанавливаю бюджет 777 ₽. Сейчас всё настрою!"
    )
    answer = await interpret(
        "бюджет как у соседа", KNOWN, RULES, None, always, langchain_agent=agent
    )
    assert answer.command.kind is Kind.UNKNOWN
    assert "Принял" not in answer.reply and "777" not in answer.reply
    agent.invoke.assert_not_called()


async def test_small_talk_reply_with_foreign_numbers_is_replaced() -> None:
    from unittest.mock import AsyncMock

    from adhelper.commands.application.langchain_agent import AgentResult, LangChainChatAgent

    agent = AsyncMock(spec=LangChainChatAgent)
    agent.invoke.return_value = AgentResult(Command(Kind.UNKNOWN), "Привет! У вас 5 объявлений.")
    answer = await interpret("привет", KNOWN, RULES, None, always, langchain_agent=agent)
    assert "5" not in answer.reply
    agent.invoke.return_value = AgentResult(Command(Kind.UNKNOWN), "Привет! Чем помочь?")
    answer = await interpret("привет", KNOWN, RULES, None, always, langchain_agent=agent)
    assert answer.reply == "Привет! Чем помочь?"


def _ctx(budget: int | None = 15_000):  # type: ignore[no-untyped-def]
    from adhelper.commands.application.analytics import AnalyticsContext

    return AnalyticsContext(
        period_label="За 7 дней",
        cost_micros=0,
        impressions=0,
        clicks=0,
        conversions=0,
        budget_rubles=budget,
        spent_rubles=225,
    )


async def test_budget_multiplied_is_computed_by_code_from_current_budget() -> None:
    answer = await interpret(
        "Увеличь бюджет в два раза", KNOWN, RULES, None, always, analytics_context=_ctx()
    )
    assert answer.command.kind is Kind.SET_BUDGET and answer.command.rubles == 30_000
    confirm = answer.confirm.replace("\xa0", " ")
    assert "30 000 ₽" in confirm and "сейчас 15 000 ₽" in confirm
    half = await interpret(
        "уменьши бюджет вдвое", KNOWN, RULES, None, always, analytics_context=_ctx()
    )
    assert half.command.rubles == 7_500
    # Без известного бюджета кратность не считаем, спрашиваем сумму.
    none = await interpret(
        "увеличь бюджет в два раза", KNOWN, RULES, None, always, analytics_context=_ctx(None)
    )
    assert none.command.kind is Kind.UNKNOWN and "сумм" in none.reply


async def test_new_ad_request_explains_where_and_checks_the_profile() -> None:
    known = Known(KNOWN.ads, ("Маникюр", "Окрашивание"))
    ok = await interpret("Запусти новую рекламу про окрашивание", known, RULES, None, always)
    assert ok.command.kind is Kind.UNKNOWN and "«Реклама»" in ok.reply
    missing = await interpret("Запусти новую рекламу про окрашивание", KNOWN, RULES, None, always)
    assert missing.command.kind is Kind.UNKNOWN and "профил" in missing.reply


async def test_delete_ads_is_refused_with_the_safe_alternative() -> None:
    answer = await interpret("Удали все объявления", KNOWN, RULES, None, always)
    assert answer.command.kind is Kind.UNKNOWN
    assert "не удаляю" in answer.reply and "пауз" in answer.reply


async def test_small_talk_uses_the_interface_word_for_leads() -> None:
    from unittest.mock import AsyncMock

    from adhelper.commands.application.langchain_agent import AgentResult, LangChainChatAgent

    agent = AsyncMock(spec=LangChainChatAgent)
    agent.invoke.return_value = AgentResult(Command(Kind.UNKNOWN), "Привет! Новые заявки ждут.")
    answer = await interpret("привет", KNOWN, RULES, None, always, langchain_agent=agent)
    assert "заявк" not in answer.reply and "обращения" in answer.reply


def test_zero_budget_is_refused() -> None:
    from adhelper.commands.domain.parse import by_rules

    parsed = by_rules("бюджет 0", KNOWN, 300)
    assert parsed.command.kind is Kind.UNKNOWN and "больше нуля" in parsed.question


async def test_moderation_reference_lists_only_ads_on_review_from_the_cabinet() -> None:
    from adhelper.commands.application.analytics import AnalyticsContext

    def ctx(names: tuple[str, ...]) -> AnalyticsContext:
        return AnalyticsContext(
            period_label="За 7 дней",
            cost_micros=0,
            impressions=0,
            clicks=0,
            conversions=0,
            on_moderation=names,
        )

    answer = await interpret(
        "Какие объявления на проверке?",
        KNOWN,
        RULES,
        None,
        always,
        analytics_context=ctx(("Укладка", "Королевское бритьё")),
    )
    assert answer.command.kind is Kind.STATS
    assert (
        "2" in answer.reply and "Укладка" in answer.reply and "Королевское бритьё" in answer.reply
    )
    empty = await interpret(
        "что на модерации", KNOWN, RULES, None, always, analytics_context=ctx(())
    )
    assert "нет" in empty.reply.lower()
