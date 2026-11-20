"""Код Метрики на сайте владельца.

Здесь только поиск номера счётчика в разметке. Что визиты действительно приходят,
подтверждает API Метрики после подключения кабинета.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

# Номер счётчика: ym(N, "init"), tag.js?id=N, картинка mc.yandex.*/watch/N, старый yaCounterN
# и SSR-конфиг Nuxt/Next (yandexMetric: N, metrikaId: N).
_COUNTER = re.compile(
    r"""\bym\(\s*(\d{5,12})\s*,\s*["']init["']"""
    r"""|mc\.yandex\.[a-z.]{2,8}/metrika/tag\.js\?[^"'<>]*\bid=(\d{5,12})\b"""
    r"""|mc\.yandex\.[a-z.]{2,8}/watch/(\d{5,12})"""
    r"""|\byaCounter(\d{5,12})\b"""
    r"""|(?i:\b(?:yandex_?metrica?|yandex_?metrika|metrica_?id|metrika_?id))["']?\s*:\s*["']?(\d{5,12})\b"""
)
MIN_COUNTER = 10_000
MAX_COUNTER = 999_999_999_999
MAX_COUNTERS = 20
MAX_COUNTER_NAME = 80

# Региональные счётчики: yandexCityMetric:{"1":123456789,"3":987654321}.
# Рядом лежит город сайта по умолчанию: defaultCityId:"1".
# Ключ города — число: по тому же правилу ищется город по умолчанию.
_CITY_COUNTERS = re.compile(r"""yandexCityMetric\s*:\s*(\{[^}]+\})""")
_CITY_KEY = re.compile(r"^\d{1,6}$")
_DEFAULT_CITY = re.compile(r"""defaultCityId["']?\s*:\s*["']?(\d{1,6})\b""")


class TagStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    FOUND = "found"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(frozen=True)
class TagRules:
    version: str
    max_checks_per_day: int
    max_attempts: int


@dataclass(frozen=True)
class TagResult:
    status: TagStatus
    counters: tuple[int, ...] = ()
    city_counters: dict[str, int] = field(default_factory=dict)
    default_city_id: str | None = None
    problem: str | None = None


@dataclass(frozen=True)
class StoredTagCheck:
    id: int
    url: str
    status: TagStatus
    counters: tuple[int, ...]
    problem: str | None
    finished_at: datetime | None
    counter_names: dict[int, str] = field(default_factory=dict)
    city_counters: dict[str, int] = field(default_factory=dict)
    default_city_id: str | None = None


def find_counters(html: str) -> tuple[int, ...]:
    found: list[int] = []
    for match in _COUNTER.finditer(html):
        number = int(next(group for group in match.groups() if group))
        if number not in found:
            found.append(number)
    return tuple(found)


def find_city_counters(html: str) -> dict[str, int]:
    """Региональные счётчики из конфига сайта: id города на сайте -> номер счётчика.

    Берём первый блок, из которого получились годные пары: до него может стоять
    обрезанный или чужой `yandexCityMetric`. Больше `MAX_COUNTERS` не запоминаем:
    число городов задаёт страница, а не мы.
    """
    for match in _CITY_COUNTERS.finditer(html):
        found = _city_counters_from(match.group(1))
        if found:
            return found
    return {}


def _city_counters_from(raw: str) -> dict[str, int]:
    # Конфиг бывает обычным JSON и JavaScript-объектом без кавычек у ключей.
    for data in (_json_or_none(raw), _json_or_none(re.sub(r"(\w+)\s*:", r'"\1":', raw))):
        if not isinstance(data, dict):
            continue
        found: dict[str, int] = {}
        for key, value in data.items():
            city = str(key).strip()
            if not _CITY_KEY.match(city):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if MIN_COUNTER <= number <= MAX_COUNTER:
                found[city] = number
            if len(found) >= MAX_COUNTERS:
                break
        if found:
            return found
    return {}


def find_default_city_id(html: str) -> str | None:
    """Город сайта по умолчанию: по нему выбираем счётчик основного города."""
    match = _DEFAULT_CITY.search(html)
    return match.group(1) if match else None


def _json_or_none(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def normalize_manual_counters(
    counters: Sequence[int],
    names: Mapping[int, str] | None = None,
) -> tuple[tuple[int, ...], dict[int, str]]:
    """Проверяет список счётчиков (первый — основной) и их пользовательские названия."""
    if len(counters) > MAX_COUNTERS:
        raise ValueError(f"Можно указать не более {MAX_COUNTERS} счётчиков Метрики")
    unique: list[int] = []
    for number in counters:
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("Номер счётчика Метрики должен состоять из 5–12 цифр")
        if number < MIN_COUNTER or number > MAX_COUNTER:
            raise ValueError("Номер счётчика Метрики должен состоять из 5–12 цифр")
        if number not in unique:
            unique.append(number)
    clean_names: dict[int, str] = {}
    if names:
        for number in unique:
            raw_name = names.get(number, "")
            if not isinstance(raw_name, str):
                continue
            cleaned = " ".join(raw_name.split())
            if len(cleaned) > MAX_COUNTER_NAME:
                raise ValueError(
                    f"Название счётчика не должно быть длиннее {MAX_COUNTER_NAME} символов"
                )
            if cleaned:
                clean_names[number] = cleaned
    return tuple(unique), clean_names
