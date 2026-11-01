"""Доставка тревог владельцу и привязка Telegram.

Тревога и её отправки пишутся в одной транзакции, поэтому отправка не теряется при падении.
Доставка «хотя бы один раз»: сбой между отправкой и отметкой даст дубль, а не пропуск.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from uuid import UUID

from adhelper.notifications.domain.messages import compose
from adhelper.notifications.domain.model import Channel, Message, next_attempt
from adhelper.notifications.domain.ports import (
    MoneySource,
    Outbox,
    Sender,
    TelegramBot,
    TelegramLinks,
)

logger = logging.getLogger(__name__)

BATCH = 20
LINK_TTL = timedelta(minutes=15)
LINKED = Message("", "Готово: сюда будут приходить тревоги о вашей рекламе из Ad Helper.")
STALE_LINK = Message(
    "", "Ссылка устарела или уже использована. Получите новую в кабинете Ad Helper."
)
UNKNOWN = Message("", "Этот бот только присылает тревоги. Подключите его в кабинете Ad Helper.")


async def deliver_pending(
    outbox: Outbox,
    senders: Mapping[str, Sender],
    money: MoneySource,
    now: datetime,
    cabinet_url: str,
) -> int:
    """Одна порция отправок. Каналы без настроек ждут, пока их настроят."""
    sent = 0
    channels = [Channel(name) for name in senders]
    for delivery in await outbox.claim(channels, now, BATCH):
        if delivery.resolved:
            await outbox.skipped(delivery.id, "resolved")
            continue
        if delivery.address is None:
            await outbox.skipped(delivery.id, "no_address")
            continue
        try:
            facts = await money.money(delivery.business_id, now)
            message = compose(
                delivery.kind,
                delivery.business_name,
                delivery.raised_at,
                facts,
                dict(delivery.details),
                cabinet_url,
            )
            await senders[delivery.channel].send(delivery.address, message)
        except Exception as error:
            # В логе только тип: текст ошибки может содержать адрес или токен бота.
            reason = type(error).__name__
            retry_at = next_attempt(delivery.attempts, now)
            logger.warning("Уведомление %s не доставлено: %s", delivery.id, reason)
            if retry_at is None:
                await outbox.failed(delivery.id, reason)
            else:
                await outbox.retry(delivery.id, retry_at, reason)
            continue
        await outbox.sent(delivery.id, now)
        sent += 1
    return sent


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def start_telegram_link(
    links: TelegramLinks,
    user_id: UUID,
    bot_name: str,
    now: datetime,
    token: Callable[[], str] = lambda: secrets.token_urlsafe(16),
) -> str:
    """Одноразовая ссылка на бота. В базе только хэш кода: утечка базы не привяжет чужой чат."""
    code = token()
    await links.start(user_id, _hash(code), now + LINK_TTL)
    return f"https://t.me/{bot_name}?start={code}"


async def handle_telegram_updates(bot: TelegramBot, links: TelegramLinks, now: datetime) -> int:
    """Сообщения боту — данные: ищем только код привязки, остальное не выполняем."""
    linked = 0
    updates = await bot.updates(await links.offset())
    for update in updates:
        if update.chat_id is None:
            continue
        command, _, code = update.text.strip().partition(" ")
        if command != "/start":
            reply = UNKNOWN
        elif code and await links.finish(_hash(code.strip()), update.chat_id, now):
            reply = LINKED
            linked += 1
        else:
            reply = STALE_LINK
        try:
            await bot.send(str(update.chat_id), reply)
        except Exception as error:
            logger.warning("Ответ боту не отправлен: %s", type(error).__name__)
    if updates:
        await links.save_offset(max(u.update_id for u in updates) + 1)
    return linked
