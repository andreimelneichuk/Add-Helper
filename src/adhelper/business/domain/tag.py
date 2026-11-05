"""Код Метрики на сайте владельца (docs/03-integrations.md §3, шаг «Проверка установки»).

Здесь только поиск номера счётчика в разметке. Что визиты действительно приходят,
подтверждает API Метрики после подключения кабинета.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# Номер счётчика: ym(N, "init"), картинка mc.yandex.*/watch/N и старый yaCounterN.
_COUNTER = re.compile(
    r"""\bym\(\s*(\d{5,12})\s*,\s*["']init["']"""
    r"""|mc\.yandex\.[a-z.]{2,8}/watch/(\d{5,12})"""
    r"""|\byaCounter(\d{5,12})\b"""
)


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
    problem: str | None = None


@dataclass(frozen=True)
class StoredTagCheck:
    id: int
    url: str
    status: TagStatus
    counters: tuple[int, ...]
    problem: str | None
    finished_at: datetime | None


def find_counters(html: str) -> tuple[int, ...]:
    found: list[int] = []
    for match in _COUNTER.finditer(html):
        number = int(next(group for group in match.groups() if group))
        if number not in found:
            found.append(number)
    return tuple(found)
