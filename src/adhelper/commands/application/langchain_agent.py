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
from pydantic import BaseModel, Field, SecretStr

from adhelper.commands.application.analytics import AnalyticsContext
from adhelper.commands.domain.model import Command, Kind, Known, KnownAd, Source
from adhelper.stats.domain.today import rub

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
            "Строго null для приветствий ('Привет', 'Здравствуйте'), благодарностей, советов и обычных бесед. "
            "Заполняй ТОЛЬКО если пользователь прямо просит выполнить действие или запросил отчет."
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
    "КОНТЕКСТ ТЕКУЩЕГО БИЗНЕСА:\n"
    "{business_context}\n\n"
    "СТАТИСТИКА ЗА 7 ДНЕЙ:\n"
    "{analytics_context}\n\n"
    "ВАЖНЫЕ ПРАВИЛА:\n"
    "- На приветствия ('привет', 'здравствуйте') и обычные вопросы action ВСЕГДА null.\n"
    "- Никогда не говори 'этого я пока не умею' на обычные человеческие фразы и приветствия.\n"
    "- Будь лаконичен, доброжелателен и полезен (2-4 емких предложения).\n"
    "- Если пользователь называет число без команды, уточни, это бюджет или новая цена услуги."
)


class LangChainChatAgent:
    """Диалоговый агент на LangChain для свободного общения и управления рекламой."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self._llm = llm

    async def invoke(
        self,
        message: str,
        known: Known,
        analytics_context: AnalyticsContext | None = None,
    ) -> AgentResult:
        ads_text = (
            "\n".join(f"- ID: {ad.id}, Заголовок: «{ad.name}»" for ad in known.ads)
            if known.ads
            else "Нет активных объявлений."
        )
        services_text = (
            ", ".join(known.services) if known.services else "Услуги в профиле не указаны."
        )
        business_context = f"Объявления:\n{ads_text}\nУслуги бизнеса: {services_text}"

        if analytics_context:
            stats_text = (
                f"Период: {analytics_context.period_label}\n"
                f"Показы: {analytics_context.impressions}, Клики: {analytics_context.clicks}, "
                f"Заявки: {analytics_context.conversions}, "
                f"Потрачено: {rub(analytics_context.cost_micros // 1_000_000)}"
            )
        else:
            stats_text = "Данные о показах отсутствуют."

        system_prompt = (
            SYSTEM_TEMPLATE.format(
                business_context=business_context,
                analytics_context=stats_text,
            )
            + "\nОтвечай строго в формате JSON по схеме: "
            '{"reply": "...", "action": null, "rubles": null, '
            '"service": null, "ad_id": null, "days": [], "wish": null}'
        )

        try:
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=message),
                ]
            )
            raw = str(response.content).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            if isinstance(data, dict):
                output = AgentOutput.model_validate(data)
                return self._to_result(output, known)
        except Exception as error:
            logger.warning(
                "Ошибка вызова LangChain агента: %s (ответ: %s)",
                type(error).__name__,
                raw if "raw" in locals() else "no raw",
            )

        # Fallback при сбое вызова LLM
        return AgentResult(
            command=Command(Kind.UNKNOWN),
            reply=(
                "Здравствуйте! Я ваш ассистент по рекламе. Сейчас на линии небольшая задержка, "
                "но я готов помочь с настройкой объявлений или ответить на вопросы."
            ),
            source=Source.MODEL,
        )

    def _to_result(self, output: AgentOutput, known: Known) -> AgentResult:
        reply = output.reply.strip()
        action = output.action

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

        target_ad: KnownAd | None = None
        if output.ad_id:
            for ad in known.ads:
                if ad.id == output.ad_id or output.ad_id in ad.id:
                    target_ad = ad
                    break

        target_service = output.service
        if target_service and target_service not in known.services:
            for s in known.services:
                if s.lower() == target_service.lower():
                    target_service = s
                    break

        command = Command(
            kind=kind,
            rubles=output.rubles,
            ad=target_ad,
            service=target_service,
            days=tuple(output.days),
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
