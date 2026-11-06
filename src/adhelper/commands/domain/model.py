"""Закрытый набор команд чата: docs/01-product-spec.md, docs/12-agents.md §4 «Разборщик команд».

Команда — это намерение, а не действие. Выполняет её владелец подтверждением, через те же
маршруты, что и кнопки: политика, журнал, исполнитель. Здесь только разбор и проверка.
"""

from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    SET_BUDGET = "set_budget"
    SET_PRICE = "set_price"
    SKIP_DAYS = "skip_days"
    AD_OFF = "ad_off"
    AD_ON = "ad_on"
    REWORK_AD = "rework_ad"
    UNDO_LAST = "undo_last"
    STATS = "stats"
    HISTORY = "history"
    UNKNOWN = "unknown"


# Эти команды меняют рекламу или профиль: без «да» владельца не выполняются.
CHANGING = frozenset(
    {
        Kind.PAUSE,
        Kind.RESUME,
        Kind.SET_BUDGET,
        Kind.SET_PRICE,
        Kind.SKIP_DAYS,
        Kind.AD_OFF,
        Kind.AD_ON,
        Kind.UNDO_LAST,
    }
)


class Source(StrEnum):
    RULES = "rules"
    MODEL = "model"


@dataclass(frozen=True)
class KnownAd:
    """Объявление площадки, которое владелец может назвать словами."""

    id: str
    name: str


@dataclass(frozen=True)
class Known:
    """Что код знает о бизнесе: только это может стать параметром команды."""

    ads: tuple[KnownAd, ...]
    services: tuple[str, ...]


@dataclass(frozen=True)
class Command:
    kind: Kind
    rubles: int | None = None
    ad: KnownAd | None = None
    service: str | None = None
    days: tuple[int, ...] = ()
    wish: str = ""


@dataclass(frozen=True)
class CommandRules:
    version: str
    max_chars: int
    model_max_tokens: int
    model_calls_per_day: int
    # Просьб в одном сообщении; в commands-1 — одна.
    max_requests: int = 1


@dataclass(frozen=True)
class Parsed:
    """Итог разбора. `question` — уточнение вместо команды: суммы и названия не додумываем."""

    command: Command
    source: Source
    question: str = ""
    reply: str = ""
