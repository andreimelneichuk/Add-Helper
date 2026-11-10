"""Закрытый набор команд чата.

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
    # Справка о бюджете: ответ пишет код по данным кабинета, команды на площадку нет.
    BUDGET = "budget"
    # Справка: какие объявления сейчас на проверке у Яндекса. Ответ пишет код.
    MODERATION = "moderation"
    # Автопилот: включить (снять остановку), остановить, сменить расписание; справка — кодом.
    AUTOPILOT_ON = "autopilot_on"
    AUTOPILOT_OFF = "autopilot_off"
    AUTOPILOT_CADENCE = "autopilot_cadence"
    AUTOPILOT_REPORT = "autopilot_report"
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
        Kind.AUTOPILOT_ON,
        Kind.AUTOPILOT_OFF,
        Kind.AUTOPILOT_CADENCE,
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
    cadence: str | None = None
    wish: str = ""


@dataclass(frozen=True)
class CommandRules:
    version: str
    max_chars: int
    model_max_tokens: int
    model_calls_per_day: int
    # Просьб в одном сообщении; в commands-1 — одна.
    max_requests: int = 1
    # Бюджет месяца выше этой суммы из чата не ставим: скорее всего, лишний ноль.
    max_budget: int = 500_000
    # Сколько секунд вопрос «Применить?» остаётся в силе: потом данные могли измениться.
    confirm_ttl_seconds: int = 600


@dataclass(frozen=True)
class Parsed:
    """Итог разбора. `question` — уточнение вместо команды: суммы и названия не додумываем."""

    command: Command
    source: Source
    question: str = ""
    reply: str = ""
