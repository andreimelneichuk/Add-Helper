"""Аналитический агент (Analytics / Info Agent): docs/tasks/13-chat-assistant-verification.md §2.В.

Read-Only консультант свободного диалога:
- Отвечает на вопросы о рекламе, метриках, эффективности объявлений и истории автопилота.
- Опирается на реальные цифры и события из витрины статистики и журнала изменений.
- Строгий запрет на выдачу управляющих команд или предложений изменений.
- Детерминированный fallback при недоступности модели.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from adhelper.commands.domain.model import Kind, Source
from adhelper.creatives.domain.model import ModelUnavailable, TextModel
from adhelper.stats.domain.today import leads_word, rub

logger = logging.getLogger(__name__)

ANALYTICS_SYSTEM = (
    "Ты — аналитический ИИ-ассистент (Analytics / Info Agent) сервиса рекламы Директ. "
    "Твоя задача — простым, живым, дружелюбным языком объяснять владельцу малого бизнеса "
    "текущее состояние рекламы, эффективность объявлений и действия автопилота.\n\n"
    "СТРОГИЕ ПРАВИЛА БЕЗОПАСНОСТИ (ГАЙДЛАЙНЫ):\n"
    "1. Ты РАБОТАЕШЬ ТОЛЬКО В РЕЖИМЕ ЧТЕНИЯ (Read-Only). Категорически запрещено генерировать\n"
    "предложения изменений рекламы или команды управления. Если пользователь просит изменить\n"
    "рекламу (например, 'а почему бюджет не 30к? сделай 30к'), вежливо подскажи,\n"
    "что для изменения нужно написать команду, например: 'бюджет 30 000' или 'пауза'.\n"
    "2. Говори языком владельца бизнеса без сухих терминов: вместо 'конверсии' используй\n"
    "'обращения' или 'заявки', вместо 'CPA' — 'цена обращения', вместо 'кампании' —\n"
    "'рекламные направления'.\n"
    "3. Опирайся строго на цифры и факты из блока <analytics_context>. Не выдумывай числа.\n"
    "4. Ответ должен быть емким (2–4 предложения), понятным и полезным.\n\n"
    "Отвечай ТОЛЬКО JSON объектом со структурой:\n"
    "{\n"
    '  "reply": "Твой связный ответ владельцу бизнеса на русском языке",\n'
    '  "suggested_view": "stats" | "history" | "ads" | "overview" | null\n'
    "}"
)

ANALYTICS_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "suggested_view": {
            "type": ["string", "null"],
            "enum": ["stats", "history", "ads", "overview", None],
        },
    },
    "required": ["reply"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AdStatSummary:
    id: str
    title: str
    impressions: int
    clicks: int
    conversions: int
    cost_micros: int
    ctr_percent: float


@dataclass(frozen=True)
class AnalyticsContext:
    """Контекст для аналитического агента (Read-Only срезы)."""

    period_label: str
    cost_micros: int
    impressions: int
    clicks: int
    conversions: int
    budget_rubles: int | None = None
    spent_rubles: int | None = None
    ads: tuple[AdStatSummary, ...] = ()
    recent_history: tuple[dict[str, Any], ...] = ()
    has_campaigns: bool = False
    connected_account: str | None = None
    wizard_ads: tuple[str, ...] = ()
    # Названия объявлений, которые сейчас на проверке у Яндекса.
    on_moderation: tuple[str, ...] = ()
    # Готовая справка об автопилоте: положение, расписание, последние решения. Пишет код.
    autopilot: str = ""


@dataclass(frozen=True)
class AnalyticsAnswer:
    reply: str
    kind: Kind
    suggested_view: str | None = None
    source: Source = Source.MODEL


class AnalyticsAgent:
    """Read-Only агент, объясняющий метрики и историю действий человеческим языком."""

    def __init__(self, model: TextModel | None = None) -> None:
        self._model = model

    async def answer(
        self,
        message: str,
        context: AnalyticsContext,
        target_kind: Kind = Kind.STATS,
        max_tokens: int = 500,
    ) -> AnalyticsAnswer:
        """Связно отвечает на аналитический вопрос по модели или через fallback."""
        if not self._model:
            return self._fallback(message, context, target_kind)

        user_content = json.dumps(
            {
                "question": message,
                "analytics_context": {
                    "period": context.period_label,
                    "cost_formatted": rub(context.cost_micros),
                    "impressions": context.impressions,
                    "clicks": context.clicks,
                    "leads": context.conversions,
                    "avg_cost_per_lead": (
                        rub(context.cost_micros // context.conversions)
                        if context.conversions > 0
                        else "нет данных"
                    ),
                    "budget_month": (
                        f"{context.budget_rubles} ₽" if context.budget_rubles else None
                    ),
                    "spent_month": f"{context.spent_rubles} ₽" if context.spent_rubles else None,
                    "ads": [
                        {
                            "title": ad.title,
                            "impressions": ad.impressions,
                            "clicks": ad.clicks,
                            "leads": ad.conversions,
                            "cost": rub(ad.cost_micros),
                            "ctr": f"{ad.ctr_percent:.1f}%",
                        }
                        for ad in context.ads[:5]
                    ],
                    "recent_actions": list(context.recent_history[:5]),
                },
            },
            ensure_ascii=False,
        )

        try:
            reply = await self._model.complete_json(
                ANALYTICS_SYSTEM,
                user_content,
                ANALYTICS_SCHEMA,
                max_tokens,
            )
            data = json.loads(reply.text)
            if isinstance(data, dict) and data.get("reply"):
                suggested = data.get("suggested_view")
                kind = (
                    Kind.HISTORY
                    if suggested == "history" or target_kind is Kind.HISTORY
                    else Kind.STATS
                )
                return AnalyticsAnswer(
                    reply=str(data["reply"]),
                    kind=kind,
                    suggested_view=str(suggested) if suggested else None,
                    source=Source.MODEL,
                )
        except (ModelUnavailable, ValueError, KeyError) as err:
            logger.warning(
                "Analytics Agent не ответил: %s, перехожу на fallback", type(err).__name__
            )

        return self._fallback(message, context, target_kind)

    def _fallback(
        self,
        message: str,
        context: AnalyticsContext,
        target_kind: Kind,
    ) -> AnalyticsAnswer:
        """Детерминированная связная сводка на базе реальных данных."""
        text = message.lower()
        if target_kind is Kind.HISTORY or any(
            w in text for w in ("что ты делал", "истори", "менял", "сделал")
        ):
            if not context.recent_history:
                return AnalyticsAnswer(
                    reply="В последнее время изменений не было: реклама работает штатно.",
                    kind=Kind.HISTORY,
                    suggested_view="history",
                    source=Source.RULES,
                )
            items = []
            for item in context.recent_history[:3]:
                expl = item.get("explanation") or item.get("kind") or "действие"
                items.append(f"• {expl}")
            summary = "Вот последние действия автопилота:\n" + "\n".join(items)
            return AnalyticsAnswer(
                reply=summary,
                kind=Kind.HISTORY,
                suggested_view="history",
                source=Source.RULES,
            )

        cost_str = rub(context.cost_micros)
        leads = context.conversions
        lw = leads_word(leads)

        if leads > 0:
            cpa = rub(context.cost_micros // leads)
            lead_info = f"{leads} {lw} по средней цене {cpa}"
        else:
            lead_info = "пока без обращений, собираем клики и оптимизируем ставки"

        reply = (
            f"{context.period_label}: потрачено {cost_str}, получено {context.clicks} кликов "
            f"({lead_info})."
        )

        if context.ads:
            best_ad = max(context.ads, key=lambda a: (a.conversions, a.clicks))
            if best_ad.clicks > 0:
                reply += f" Лучше всего работает объявление «{best_ad.title}»."

        return AnalyticsAnswer(
            reply=reply,
            kind=Kind.STATS,
            suggested_view="stats",
            source=Source.RULES,
        )
