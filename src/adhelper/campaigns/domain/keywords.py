"""Генерация семантического ядра и ключевых фраз для кампаний в Директе.

Детерминированный подбор поисковых запросов по фактам профиля бизнеса:
услуги, ниша, город. От 5 до 15 целевых двух- и трёхсловных фраз на услугу,
очистка от мусорных слов и добавление автотаргетинга Яндекса.
"""

import json
import re
from collections.abc import Sequence
from typing import Any

AUTOTARGETING_KEYWORD = "---autotargeting"

# Мусорные слова и хвосты, исключаемые из семантики коммерческих услуг
STOP_WORDS = frozenset(
    {
        "бесплатно",
        "даром",
        "скачать",
        "своими",
        "руками",
        "домашних",
        "условиях",
        "самостоятельно",
        "видео",
        "фото",
        "картинки",
        "вакансии",
        "работа",
        "обучение",
        "курсы",
        "реферат",
        "песня",
        "клип",
        "порно",
        "бу",
        "б/у",
        "авито",
        "юла",
    }
)

# Общие коммерческие уточнения: подходят любому бизнесу.
UNIVERSAL_MODIFIERS = ("цена", "стоимость", "недорого", "рядом", "отзывы")

# Салонам и мастерам: запись и есть покупка.
SALON_MODIFIERS = ("салон", "записаться", "запись")

# Доставке еды: заказ и доставка, а не визит в салон.
DELIVERY_MODIFIERS = ("доставка", "заказать")

SALON_WORDS = (
    "салон",
    "красот",
    "парикмахер",
    "барбер",
    "маникюр",
    "педикюр",
    "бров",
    "ресниц",
    "косметолог",
    "массаж",
    "эпиляц",
    "визаж",
    "стрижк",
)

DELIVERY_WORDS = (
    "доставк",
    "еда",
    "еды",
    "общепит",
    "кафе",
    "ресторан",
    "пицц",
    "суши",
    "роллы",
)

# Единицы измерения из названий товаров: «сет 1208 г / 48 шт» — это вес и количество,
# а не часть названия. В поисковую фразу они не идут.
UNIT_WORDS = frozenset(
    {"г", "гр", "кг", "мл", "л", "шт", "см", "мм", "уп", "порц", "порция", "кал"}
)

# Слова, которые приводят не покупателей, а читателей и соискателей.
BASIC_NEGATIVE = (
    "бесплатно",
    "своими руками",
    "работа",
    "вакансии",
    "обучение",
    "курсы",
    "авито",
    "юла",
    "скачать",
    "фото",
    "видео",
)

DELIVERY_NEGATIVE = (
    *BASIC_NEGATIVE,
    "рецепт",
    "рецепты",
    "как приготовить",
    "приготовление",
    "калории",
    "калорийность",
    "состав",
    "оптом",
    "франшиза",
)

SALON_NEGATIVE = (*BASIC_NEGATIVE, "сделать самой", "пошагово", "схема")


def _mentions(niche: str, words: tuple[str, ...]) -> bool:
    """Ищет слово или основу слова целиком, а не внутри другого: «победа» — не еда."""
    padded = f" {re.sub(r'[^\w]+', ' ', niche.casefold()).strip()} "
    return any(f" {word}" in padded for word in words)


def _modifiers_for(niche: str) -> tuple[str, ...]:
    """Уточнения по нише. Неизвестная ниша получает только общие слова.

    «Салон» и «записаться» у доставки еды собирают мусорные запросы, поэтому ниша
    выбирает их явно, а не получает по умолчанию.
    """
    if _mentions(niche, DELIVERY_WORDS):
        return UNIVERSAL_MODIFIERS + DELIVERY_MODIFIERS
    if _mentions(niche, SALON_WORDS):
        return UNIVERSAL_MODIFIERS + SALON_MODIFIERS
    return UNIVERSAL_MODIFIERS


def negative_keywords(niche: str) -> tuple[str, ...]:
    """Минус-слова кампании: что отсечь, чтобы бюджет не уходил читателям."""
    if _mentions(niche, DELIVERY_WORDS):
        return DELIVERY_NEGATIVE
    if _mentions(niche, SALON_WORDS):
        return SALON_NEGATIVE
    return BASIC_NEGATIVE


def _with_modifier(base: str, modifier: str, city: str = "") -> str:
    if modifier in ("записаться", "запись"):
        phrase = f"{modifier} на {base}"
    elif modifier in ("доставка", "заказать"):
        phrase = f"{modifier} {base}"
    else:
        phrase = f"{base} {modifier}"
    return f"{phrase} {city}".strip()


KEYWORDS_EXPANSION_SYSTEM = (
    "Ты — специалист по контекстной рекламе в Яндекс Директе.\n"
    "Для каждого названия услуги малого бизнеса подбери от 1 до 3 реальных поисковых\n"
    "синонимов или разговорных формулировок, как обычные люди ищут эту услугу в Яндексе\n"
    "(например, для «покрытие гель-лак» -> «шеллак», «маникюр с покрытием»;\n"
    "для «чистка лица» -> «пилинг», «ультразвуковая чистка»).\n"
    "Требования:\n"
    "- Каждая фраза должна состоять из 1–3 слов.\n"
    "- Только строчные буквы.\n"
    "- Без названий брендов-конкурентов.\n"
    "- Запрещено использовать слова: бесплатно, даром, своими руками, скачать,\n"
    "  обучение, вакансии, курсы, авито, юла, фото, видео.\n"
    "- Верни результат в формате JSON строго по схеме."
)

KEYWORDS_EXPANSION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "synonyms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "terms": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["service", "terms"],
            },
        }
    },
    "required": ["synonyms"],
}


def parse_synonyms_response(reply_text: str) -> list[str]:
    """Разбирает и фильтрует синонимы услуг из ответа языковой модели."""
    if not reply_text:
        return []
    cleaned_json = reply_text.strip()
    if cleaned_json.startswith("```"):
        cleaned_json = re.sub(r"^```(?:json)?\s*", "", cleaned_json)
        cleaned_json = re.sub(r"\s*```$", "", cleaned_json)
    try:
        data = json.loads(cleaned_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Попытка очистить завершающие запятые перед ] и }, свойственные ответам LLM
        cleaned_json = re.sub(r",\s*([\]}])", r"\1", cleaned_json)
        try:
            data = json.loads(cleaned_json)
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    if not isinstance(data, dict):
        return []

    synonyms_raw = data.get("synonyms")
    if not isinstance(synonyms_raw, list):
        return []

    collected: list[str] = []
    seen: set[str] = set()

    for item in synonyms_raw:
        if not isinstance(item, dict):
            continue
        terms = item.get("terms")
        if not isinstance(terms, list):
            continue
        for term in terms:
            if not isinstance(term, str):
                continue
            cleaned = _clean_service_name(term)
            words = cleaned.split()
            if not (1 <= len(words) <= 3):
                continue
            if any(w in STOP_WORDS for w in words):
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                collected.append(cleaned)

    return collected


def _clean_service_name(name: str) -> str:
    """Очищает название услуги от цен, валют и знаков препинания."""
    text = name.lower()
    # Удаляем упоминания цен (например: "1500 ₽", "от 2000 руб")
    text = re.sub(r"(?:от\s*)?\d+[\d\s]*(?:₽|руб\.?|р\.?)?", "", text)
    # Удаляем знаки препинания и спецсимволы
    text = re.sub(r"[^\w\s-]", " ", text)
    # Нормализуем пробелы
    words = [w for w in text.split() if w not in STOP_WORDS and w not in UNIT_WORDS]
    # Ограничиваем базовое название услуги максимум 3 словами
    return " ".join(words[:3])


def _extract_city(address: str) -> str:
    """Извлекает название города из адреса или возвращает очищенную строку."""
    if not address:
        return ""
    text = address.lower().replace("ё", "е")
    # Проверяем типовые форматы "г. Пермь", "город Москва"
    match = re.search(r"(?:г(?:ород|\.)?\s*)([а-яa-z-]+)", text)
    if match:
        return match.group(1).strip()
    # Если запятые разделяют адрес, первое или второе слово часто город
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts:
        first = re.sub(r"^(?:россия|рф)\b", "", parts[0]).strip()
        words = first.split()
        if words:
            return words[0]
    return ""


def generate_keywords(
    services: Sequence[Any],
    niche: str = "",
    city: str = "",
    address: str = "",
    include_autotargeting: bool = True,
    max_per_service: int = 15,
) -> tuple[str, ...]:
    """Генерирует целевые поисковые запросы по списку услуг, нише и городу.

    Каждая фраза состоит из 2–4 слов без стоп-слов.
    При include_autotargeting=True первым элементом возвращается `---autotargeting`.
    """
    detected_city = (city or _extract_city(address)).lower().strip()
    modifiers = _modifiers_for(niche)
    results: list[str] = []
    seen: set[str] = set()

    def add(phrase: str) -> None:
        phrase = phrase.strip()
        words = phrase.split()
        if len(words) < 2 or len(words) > 5:
            return
        if any(w in STOP_WORDS for w in words):
            return
        if phrase not in seen:
            seen.add(phrase)
            results.append(phrase)

    if include_autotargeting:
        results.append(AUTOTARGETING_KEYWORD)
        seen.add(AUTOTARGETING_KEYWORD)

    for item in services:
        name = getattr(item, "name", str(item))
        base = _clean_service_name(name)
        if not base:
            continue

        service_added = 0

        # 1. Прямые запросы с городом
        if detected_city and detected_city not in base:
            city_phrase = f"{base} {detected_city}"
            add(city_phrase)
            service_added += 1

            for mod in modifiers:
                if service_added >= max_per_service:
                    break
                add(_with_modifier(base, mod, detected_city))
                service_added += 1

        # 2. Коммерческие запросы без города (для локального радиуса / поиска)
        for mod in modifiers:
            if service_added >= max_per_service:
                break
            add(_with_modifier(base, mod))
            service_added += 1

    # Если услуг не было, но указана ниша — добавляем общие запросы ниши
    if not results or (len(results) == 1 and results[0] == AUTOTARGETING_KEYWORD):
        clean_niche = _clean_service_name(niche)
        if clean_niche:
            if detected_city:
                add(f"{clean_niche} {detected_city}")
                add(f"{clean_niche} цены {detected_city}")
                add(f"{clean_niche} рядом {detected_city}")
            else:
                add(f"{clean_niche} цены")
                add(f"{clean_niche} рядом")

    return tuple(results)
