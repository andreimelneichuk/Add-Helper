from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class Channel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"


@dataclass(frozen=True)
class Message:
    subject: str
    text: str


@dataclass(frozen=True)
class MoneyFacts:
    """Деньги текущего периода для объяснения тревоги. Все суммы в микроединицах."""

    hard_limit: int
    spendable: int
    tolerance: int
    spent: int | None = None
    last_balance: int | None = None
    balance_gap: int | None = None


@dataclass(frozen=True)
class Delivery:
    """Одна отправка одной тревоги в один канал. Адрес берётся на момент отправки."""

    id: int
    business_id: Any
    channel: Channel
    attempts: int
    kind: str
    business_name: str
    raised_at: datetime
    resolved: bool
    # Почта или чат Telegram. None — владелец отвязал канал после тревоги.
    address: str | None
    details: Mapping[str, Any] = field(default_factory=dict)


# Сколько ждать перед повтором после неудачной попытки с этим номером.
RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
    timedelta(hours=1),
)
# Отправку, которая шла дольше, считаем прерванной и берём заново.
SENDING_LEASE = timedelta(minutes=5)


def next_attempt(attempts: int, now: datetime) -> datetime | None:
    """Когда повторить после неудачной попытки номер `attempts`; None — попытки кончились."""
    if attempts > len(RETRY_DELAYS):
        return None
    return now + RETRY_DELAYS[attempts - 1]


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    # None — не личный чат или не сообщение: такое пропускаем, но сдвигаем очередь.
    chat_id: int | None
    text: str
