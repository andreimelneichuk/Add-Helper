"""Генерация семантического ядра и ключевых фраз для кампаний в Директе.

Детерминированный подбор поисковых запросов по фактам профиля бизнеса:
услуги, ниша, город. От 5 до 15 целевых двух- и трёхсловных фраз на услугу,
очистка от мусорных слов и добавление автотаргетинга Яндекса.
"""

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

# Коммерческие модификаторы для формирования целевых запросов
INTENT_MODIFIERS = (
    "цена",
    "стоимость",
    "записаться",
    "запись",
    "рядом",
    "недорого",
    "отзывы",
    "салон",
)


def _clean_service_name(name: str) -> str:
    """Очищает название услуги от цен, валют и знаков препинания."""
    text = name.lower()
    # Удаляем упоминания цен (например: "1500 ₽", "от 2000 руб")
    text = re.sub(r"(?:от\s*)?\d+[\d\s]*(?:₽|руб\.?|р\.?)?", "", text)
    # Удаляем знаки препинания и спецсимволы
    text = re.sub(r"[^\w\s-]", " ", text)
    # Нормализуем пробелы
    words = [w for w in text.split() if w not in STOP_WORDS]
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

            for mod in INTENT_MODIFIERS:
                if service_added >= max_per_service:
                    break
                if mod in ("записаться", "запись"):
                    add(f"{mod} на {base} {detected_city}")
                else:
                    add(f"{base} {mod} {detected_city}")
                service_added += 1

        # 2. Коммерческие запросы без города (для локального радиуса / поиска)
        for mod in INTENT_MODIFIERS:
            if service_added >= max_per_service:
                break
            if mod in ("записаться", "запись"):
                add(f"{mod} на {base}")
            else:
                add(f"{base} {mod}")
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
