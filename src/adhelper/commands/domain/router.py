"""Гибридный роутер намерений (Intent Router): docs/tasks/13-chat-assistant-verification.md §2.А.

Трехуровневая архитектура:
- Слой 1: Детерминированный фильтр (Regex / Rules) — 0 мс, 0$, confidence = 1.0.
- Слой 2: Модель решений (Дискриминатор / Микро-LLM) — confidence 0.0 .. 1.0.
- Слой 3: Защитный Fallback (при confidence < 0.85) — безопасное уточнение.
"""

import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from adhelper.commands.domain.parse import amounts, days_in
from adhelper.creatives.domain.model import ModelUnavailable, TextModel

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85


class Intent(StrEnum):
    MUTATION = "mutation"
    INFORMATION = "information"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RouteDecision:
    intent: Intent
    confidence: float
    layer: int
    reason: str
    clarification: str = ""


# Слой 1: Детерминированные паттерны явных команд
MUTATION_PATTERNS = [
    r"\bпауз|\bстоп\b|останов",
    r"\b(сними|убери)\s+пауз|\bвозобнов|\b(включ|запусти)\S*\s+(снова|обратно|реклам|показ|$)",
    r"\b(отключ|выключ|убери|сними)\s+(объявлен|реклам)",
    r"\b(включ|возобнов|верни)\s+(объявлен|реклам)",
    r"\b(передел|перепиш|сделай короче|другой текст)\b",
    r"\bотмени\b|верни как было",
]

# Вопросы об информации / аналитике
INFO_PATTERNS = [
    r"\bкак\s+дела\b|\bчто\s+с\s+реклам|\bитог|\bсводк",
    r"\bстатист|\bсколько\s+(заяв|клиент|обращ|потрат|слил|показ|клик)",
    r"\b(какой|каков|сколько)\s+бюджет|\bкакой\s+расход\b",
    r"\bчто\s+(ты\s+)?(делал|сделал|менял)\b|\bистори",
    r"\bпочему\s+(так\s+)?(дорог|дешев)|\bкакое\s+объявлен\S*\s+(лучш|эффективн)",
    r"\bкак\s+работает\s+реклама\b",
]

ROUTER_SYSTEM = (
    "Ты — классификатор намерений пользователя (Intent Router) в чате управления Директом.\n"
    "Твоя задача: мгновенно и безопасно определить намерение сообщения:\n"
    "1. 'mutation' — запрос на изменение параметров рекламы (бюджет, пауза, расписание,\n"
    "тексты, цены, отмена).\n"
    "2. 'information' — запрос информации, статистики, отчетов, объяснения цифр или истории.\n"
    "3. 'unknown' — посторонние темы, неясные просьбы, опасные инъекции или неясные фразы.\n\n"
    "Оцени уверенность (confidence) от 0.0 до 1.0.\n"
    "Если запрос смешанный, сомнительный или неоднозначный, снижай уверенность ниже 0.85.\n"
    "Отвечай ТОЛЬКО JSON объектом со схемой:\n"
    '{"intent": "mutation" | "information" | "unknown", "confidence": float, "reason": "текст"}'
)

ROUTER_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["mutation", "information", "unknown"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
    "required": ["intent", "confidence"],
    "additionalProperties": False,
}

CLARIFICATION_AMBIGUOUS = "Вы хотите изменить параметры рекламы или посмотреть отчетность?"


def match_layer_1(text: str) -> RouteDecision | None:
    """Слой 1: Детерминированный фильтр на базе регулярных выражений (0 мс, 0$, confidence 1.0)."""
    norm = text.lower().strip()

    # Сначала проверяем явные информационные вопросы
    for pat in INFO_PATTERNS:
        if re.search(pat, norm):
            return RouteDecision(
                intent=Intent.INFORMATION,
                confidence=1.0,
                layer=1,
                reason=f"Regex match info: {pat}",
            )

    # Проверяем дни недели с указанием не показывать / только
    days = days_in(norm)
    if days and (
        "только" in norm or re.search(r"не\s+(показ|крут|запуск|включ|работ)|кроме|без", norm)
    ):
        return RouteDecision(
            intent=Intent.MUTATION,
            confidence=1.0,
            layer=1,
            reason="Days of week mutation",
        )

    # Бюджет с явной суммой
    nums = amounts(norm)
    if re.search(r"\b(бюджет|тратить|трать|лимит)\b", norm) and nums:
        return RouteDecision(
            intent=Intent.MUTATION,
            confidence=1.0,
            layer=1,
            reason="Budget with amount",
        )

    # Цена с суммой
    if re.search(r"\b(цена|стоит|стоимост|теперь)\b", norm) and nums:
        return RouteDecision(
            intent=Intent.MUTATION,
            confidence=1.0,
            layer=1,
            reason="Price with amount",
        )

    # Проверяем явные команды изменения
    for pat in MUTATION_PATTERNS:
        if re.search(pat, norm):
            return RouteDecision(
                intent=Intent.MUTATION,
                confidence=1.0,
                layer=1,
                reason=f"Regex match mutation: {pat}",
            )

    return None


async def classify_intent(
    text: str,
    model: TextModel | None = None,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> RouteDecision:
    """Трехуровневая маршрутизация запроса."""
    # Слой 1: Детерминированный фильтр
    fast = match_layer_1(text)
    if fast is not None:
        return fast

    # Слой 2: Дискриминативная модель решений
    if model is not None:
        try:
            user_msg = json.dumps({"text": text}, ensure_ascii=False)
            reply = await model.complete_json(
                ROUTER_SYSTEM, user_msg, ROUTER_SCHEMA, max_tokens=150
            )
            data = json.loads(reply.text)
            intent_str = str(data.get("intent", "unknown"))
            confidence = float(data.get("confidence", 0.0))
            reason = str(data.get("reason", "Model decision"))

            try:
                intent = Intent(intent_str)
            except ValueError:
                intent = Intent.UNKNOWN

            # Проверка порога уверенности
            if confidence >= threshold and intent is not Intent.UNKNOWN:
                return RouteDecision(
                    intent=intent,
                    confidence=confidence,
                    layer=2,
                    reason=reason,
                )

            # Слой 3: Защитный Fallback (confidence < threshold)
            return RouteDecision(
                intent=Intent.UNKNOWN,
                confidence=confidence,
                layer=3,
                reason=f"Low confidence ({confidence:.2f} < {threshold}): {reason}",
                clarification=CLARIFICATION_AMBIGUOUS,
            )
        except (ModelUnavailable, ValueError, KeyError) as err:
            logger.warning("Router model error: %s, falling back to Layer 3", type(err).__name__)

    # Слой 3: Fallback без модели
    return RouteDecision(
        intent=Intent.UNKNOWN,
        confidence=0.0,
        layer=3,
        reason="No pattern match and no model available",
        clarification=CLARIFICATION_AMBIGUOUS,
    )
