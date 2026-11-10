"""Полноценный диалоговый LLM-агент на LangChain для Ad Helper.

Отвечает на любые сообщения пользователя живым, естественным языком,
консультирует по рекламе, анализирует статистику и распознает команды
управления Директом.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr, field_validator

from adhelper.commands.application.analytics import AnalyticsAgent, AnalyticsContext
from adhelper.commands.domain.model import Command, Kind, Known, KnownAd, Source
from adhelper.commands.domain.parse import WEEK, amounts, days_in, normalize

logger = logging.getLogger(__name__)

ActionName = Literal[
    "pause",
    "resume",
    "set_budget",
    "set_price",
    "skip_days",
    "ad_off",
    "ad_on",
    "rework_ad",
    "undo_last",
    "stats",
    "history",
]


class AgentOutput(BaseModel):
    reply: str = Field(
        description=(
            "Живой, дружелюбный, человечный ответ владельцу бизнеса на русском языке. "
            "Никогда не используй сухие роботизированные фразы вроде 'этого я не умею'. "
            "Поздоровайся, если с тобой поздоровались; ответь на вопрос, если спросили совет; "
            "объясни метрики, если спросили о цифрах; подтверди действие, если попросили изменить."
        )
    )
    action: ActionName | None = Field(
        default=None,
        description=(
            "Строго null для приветствий, благодарностей, советов и обычных бесед. "
            "Заполняй ТОЛЬКО если пользователь прямо просит выполнить действие или отчет."
        ),
    )
    rubles: int | None = Field(
        default=None,
        description="Сумма в рублях (целое число), если меняется бюджет или цена услуги.",
    )
    service: str | None = Field(
        default=None,
        description="Точное название услуги из списка услуг бизнеса, если меняется её цена.",
    )
    ad_id: str | None = Field(
        default=None,
        description="ID объявления из списка, если действие относится к конкретному объявлению.",
    )
    days: list[int] = Field(
        default_factory=list,
        description="Номера дней недели без показов (1=Пн, 2=Вт, ..., 7=Вс).",
    )
    wish: str | None = Field(
        default=None,
        description="Пожелание к изменению текста объявления при переделке.",
    )

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, v: object) -> object:
        if isinstance(v, str) and v.lower() in ("null", "none", "", "no"):
            return None
        return v

    @field_validator("rubles", mode="before")
    @classmethod
    def normalize_rubles(cls, v: object) -> object:
        if isinstance(v, str) and v.isdigit():
            return int(v)
        if isinstance(v, str) and v.lower() in ("null", "none", ""):
            return None
        return v

    @field_validator("service", "ad_id", "wish", mode="before")
    @classmethod
    def normalize_str(cls, v: object) -> object:
        if isinstance(v, str) and v.lower() in ("null", "none", ""):
            return None
        return v


@dataclass(frozen=True)
class AgentResult:
    command: Command
    reply: str
    source: Source = Source.MODEL


SYSTEM_TEMPLATE = (
    "Ты — умный, заботливый и опытный персональный рекламный ассистент сервиса Ad Helper "
    "для малого бизнеса в Яндекс Директе.\n"
    "Ты общаешься напрямую с владельцем бизнеса на понятном, живом языке без канцелярита.\n\n"
    "ТВОИ ВОЗМОЖНОСТИ:\n"
    "1. Живое общение: на приветствия тепло здоровайся, представляйся и спрашивай, чем помочь.\n"
    "2. Консультации: отвечай на вопросы о рекламе, подсказывай, как привлечь клиентов и улучшить "
    "заголовки.\n"
    "3. Аналитика: объясняй цифры за последние 7 дней (показы, клики, заявки, расход).\n"
    "4. Управление рекламой: когда владелец просит что-то изменить, указывай action.\n\n"
    "СПИСОК ACTIONS:\n"
    "- pause: поставить всю рекламу на паузу\n"
    "- resume: включить показы обратно\n"
    "- set_budget: установить бюджет месяца (rubles: целое число)\n"
    "- set_price: изменить цену услуги (service: название из списка, rubles: новая цена)\n"
    "- skip_days: не показывать рекламу в выбранные дни (days: список 1..7)\n"
    "- ad_off / ad_on: отключить или включить конкретное объявление (ad_id: ID из списка)\n"
    "- rework_ad: переделать текст объявления (ad_id: ID из списка, wish: пожелание)\n"
    "- undo_last: отменить последнее действие автопилота или владельца\n"
    "- stats: показать подробную статистику показов и расходов\n"
    "- history: показать историю изменений\n"
    "- null: для обычного разговора, приветствия, совета или консультации.\n\n"
    "ВАЖНЫЕ ПРАВИЛА:\n"
    "- На приветствия ('привет', 'здравствуйте') и обычные вопросы action ВСЕГДА null.\n"
    "- Никогда не говори 'этого я пока не умею' на обычные человеческие фразы и приветствия.\n"
    "- Будь лаконичен, доброжелателен и полезен (2-4 емких предложения).\n"
    "- Если пользователь называет число без команды, уточни, это бюджет или новая цена услуги.\n"
    "- Параметры команд (суммы, цены, дни) бери только из сообщения пользователя. "
    "Цифры о рекламе (показы, клики, расход, бюджет) бери только из business_data: можешь "
    "пересказывать и объяснять их, но не придумывай новых. Нет данных — так и скажи.\n"
    "- Если в analytics указано has_campaigns=false, реклама в Директе ещё не запущена "
    "(кампаний 0). Если draft_ads_count > 0, объясни владельцу, что объявления уже подготовлены "
    "в мастере и ждут старта, но показы ещё не идут: для старта нужно завершить запуск рекламы. "
    "Не говори, что объявления 'выключены'."
)


def analytics_payload(known: Known, ctx: AnalyticsContext | None) -> dict[str, object]:
    """Всё, что агент вправе пересказывать: данные кабинета, посчитанные кодом."""
    payload: dict[str, object] = {
        "ads": [{"id": ad.id, "title": ad.name} for ad in known.ads],
        "services": list(known.services),
    }
    if ctx is None:
        return payload
    payload["analytics"] = {
        "period": ctx.period_label,
        "impressions": ctx.impressions,
        "clicks": ctx.clicks,
        "leads": ctx.conversions,
        "cost_rub": ctx.cost_micros // 1_000_000,
        "budget_month_rub": ctx.budget_rubles,
        "spent_month_rub": ctx.spent_rubles,
        "has_campaigns": ctx.has_campaigns,
        "cabinet_connected": bool(ctx.connected_account),
        "account_login": ctx.connected_account,
        "draft_ads_count": len(ctx.wizard_ads),
        "draft_ads": list(ctx.wizard_ads),
        "on_moderation": list(ctx.on_moderation),
        "autopilot": ctx.autopilot,
        "ads_stats": [
            {
                "title": ad.title,
                "running": ad.active,
                "impressions": ad.impressions,
                "clicks": ad.clicks,
                "leads": ad.conversions,
                "cost_rub": ad.cost_micros // 1_000_000,
                "ctr_percent": round(ad.ctr_percent, 1),
            }
            for ad in ctx.ads
        ],
        "recent_history": list(ctx.recent_history)[:10],
    }
    return payload


class LangChainChatAgent:
    """Диалоговый агент на LangChain для свободного общения и управления рекламой."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self._llm = llm

    async def invoke(
        self,
        message: str,
        known: Known,
        analytics_context: AnalyticsContext | None = None,
    ) -> AgentResult | None:
        context_payload = analytics_payload(known, analytics_context)

        user_content = json.dumps(
            {
                "user_message": message,
                "business_data": context_payload,
            },
            ensure_ascii=False,
        )

        system_prompt = (
            SYSTEM_TEMPLATE + "\nОтвечай строго в формате JSON по схеме: "
            '{"reply": "...", "action": null, "rubles": null, '
            '"service": null, "ad_id": null, "days": [], "wish": null}'
        )

        try:
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_content),
                ]
            )
            raw = str(response.content).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            if isinstance(data, dict):
                output = AgentOutput.model_validate(data)
                return self._to_result(output, message, known, analytics_context)
        except Exception as error:
            logger.warning(
                "Ошибка вызова LangChain агента: %s",
                type(error).__name__,
            )
            return None

        return None

    def _to_result(
        self,
        output: AgentOutput,
        message: str,
        known: Known,
        analytics_context: AnalyticsContext | None = None,
    ) -> AgentResult:
        reply = output.reply.strip()
        action = output.action
        text = normalize(message)

        if not action:
            return AgentResult(command=Command(Kind.UNKNOWN), reply=reply)

        kind_map = {
            "pause": Kind.PAUSE,
            "resume": Kind.RESUME,
            "set_budget": Kind.SET_BUDGET,
            "set_price": Kind.SET_PRICE,
            "skip_days": Kind.SKIP_DAYS,
            "ad_off": Kind.AD_OFF,
            "ad_on": Kind.AD_ON,
            "rework_ad": Kind.REWORK_AD,
            "undo_last": Kind.UNDO_LAST,
            "stats": Kind.STATS,
            "history": Kind.HISTORY,
        }
        kind = kind_map.get(action, Kind.UNKNOWN)
        if kind is Kind.UNKNOWN:
            return AgentResult(command=Command(Kind.UNKNOWN), reply=reply)

        # Проверка сумм: число должно присутствовать в сообщении
        valid_amounts = amounts(text)
        if kind in (Kind.SET_BUDGET, Kind.SET_PRICE) and (
            output.rubles is None or output.rubles not in valid_amounts
        ):
            return AgentResult(command=Command(Kind.UNKNOWN), reply=reply)

        # Проверка объявления: только точное равенство id
        target_ad: KnownAd | None = None
        if output.ad_id:
            for ad in known.ads:
                if ad.id == output.ad_id:
                    target_ad = ad
                    break
        if kind in (Kind.AD_OFF, Kind.AD_ON, Kind.REWORK_AD) and target_ad is None:
            return AgentResult(command=Command(Kind.UNKNOWN), reply=reply)

        # Проверка услуги
        target_service = output.service
        if target_service and target_service not in known.services:
            for s in known.services:
                if s.lower() == target_service.lower():
                    target_service = s
                    break
        if kind is Kind.SET_PRICE and (not target_service or target_service not in known.services):
            return AgentResult(command=Command(Kind.UNKNOWN), reply=reply)

        # Проверка дней
        days = tuple(sorted({d for d in output.days if isinstance(d, int) and d in WEEK}))
        if kind is Kind.SKIP_DAYS and (not days or not days_in(text)):
            return AgentResult(command=Command(Kind.UNKNOWN), reply=reply)

        # Для статистики и истории: числа подставляются проверенным кодом
        if kind in (Kind.STATS, Kind.HISTORY) and analytics_context:
            code_reply = AnalyticsAgent(None)._fallback(message, analytics_context, kind).reply
            reply = code_reply

        command = Command(
            kind=kind,
            rubles=output.rubles,
            ad=target_ad,
            service=target_service,
            days=days,
            wish=output.wish or "",
        )
        return AgentResult(command=command, reply=reply)


def build_langchain_agent(
    base_url: str,
    api_key: SecretStr | None,
    model_name: str,
    project: str = "",
) -> LangChainChatAgent | None:
    """Создать LangChain агента, если задан ключ модели."""
    if api_key is None or not api_key.get_secret_value() or not model_name:
        return None

    headers: dict[str, str] = {}
    if project:
        headers["OpenAI-Project"] = project

    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        default_headers=headers if headers else None,
        temperature=0.3,
        timeout=30.0,
        max_retries=2,
    )
    return LangChainChatAgent(llm)
