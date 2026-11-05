"""Доставка тревог на настоящем PostgreSQL: отправки рождаются вместе с тревогой и не теряются."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.notifications.adapters.money import DashboardMoney
from adhelper.notifications.adapters.sql import SqlAlerts, SqlOutbox, SqlTelegramLinks
from adhelper.notifications.application.service import (
    deliver_pending,
    handle_telegram_updates,
    start_telegram_link,
)
from adhelper.notifications.domain.model import Channel, Message, MoneyFacts, TelegramUpdate
from adhelper.notifications.domain.ports import DeliveryFailed
from adhelper.worker.demo import seed_demo
from tests.seed import seed_account

Sessions = async_sessionmaker[AsyncSession]
# Тревоги создаются по часам базы: зашитая дата перестаёт работать, как только наступит.
# Считаем перед каждым тестом: при импорте она устаревала, если прогон шёл дольше 5 минут.
NOW = datetime.now(UTC) + timedelta(minutes=5)


@pytest.fixture(autouse=True)
def _fresh_now() -> None:
    global NOW
    NOW = datetime.now(UTC) + timedelta(minutes=5)


URL = "https://adhelper.example"
M = 1_000_000


class Recorder:
    def __init__(self, failures: int = 0) -> None:
        self.sent: list[tuple[str, Message]] = []
        self.failures = failures

    async def send(self, address: str, message: Message) -> None:
        if self.failures:
            self.failures -= 1
            raise DeliveryFailed("SMTPServerDisconnected")
        self.sent.append((address, message))


class FixedMoney:
    async def money(self, business_id: Any, now: datetime) -> MoneyFacts | None:
        return MoneyFacts(40_000 * M, 36_000 * M, 800 * M, balance_gap=1_240 * M)


class FakeBot(Recorder):
    def __init__(self, updates: list[TelegramUpdate]) -> None:
        super().__init__()
        self.pending = updates
        self.offsets: list[int | None] = []

    async def updates(self, offset: int | None) -> list[TelegramUpdate]:
        self.offsets.append(offset)
        return [u for u in self.pending if offset is None or u.update_id >= offset]


async def owner(
    sessions: Sessions, email: str | None = "anna@example.test"
) -> tuple[uuid.UUID, uuid.UUID]:
    business = uuid.uuid4()
    await seed_account(sessions, business)
    async with sessions.begin() as session:
        user = await session.scalar(
            text(
                "UPDATE users SET email = :e FROM businesses b"
                " WHERE b.id = :b AND users.id = b.user_id RETURNING users.id"
            ),
            {"e": email, "b": business},
        )
    return business, user


async def statuses(sessions: Sessions) -> list[tuple[str, str, int]]:
    async with sessions() as session:
        rows = await session.execute(
            text("SELECT channel, status, attempts FROM notification_deliveries ORDER BY id")
        )
        return [(r[0], r[1], r[2]) for r in rows]


async def send(sessions: Sessions, senders: dict[str, Any], now: datetime | None = None) -> int:
    return await deliver_pending(SqlOutbox(sessions), senders, FixedMoney(), now or NOW, URL)


async def test_alert_creates_one_email_delivery_and_sends_numbers(sessions: Sessions) -> None:
    business, _ = await owner(sessions)
    alerts = SqlAlerts(sessions, business)
    await alerts.raise_alert("balance_mismatch", {})
    await alerts.raise_alert("balance_mismatch", {})  # тревога уже открыта: второго письма нет
    email = Recorder()

    assert await send(sessions, {"email": email}) == 1
    assert await send(sessions, {"email": email}) == 0
    [(address, message)] = email.sent
    assert address == "anna@example.test"
    assert "1 240 ₽" in message.text and URL in message.text
    assert await statuses(sessions) == [("email", "sent", 1)]


async def test_owner_without_email_gets_no_email(sessions: Sessions) -> None:
    business, _ = await owner(sessions, email=None)
    await SqlAlerts(sessions, business).raise_alert("zero_balance", {})
    assert await statuses(sessions) == []


async def test_failures_retry_with_backoff_then_give_up(sessions: Sessions) -> None:
    business, _ = await owner(sessions)
    await SqlAlerts(sessions, business).raise_alert("budget_stopped", {})
    email = Recorder(failures=10)

    assert await send(sessions, {"email": email}) == 0
    assert await statuses(sessions) == [("email", "pending", 1)]
    assert await send(sessions, {"email": email}, NOW + timedelta(seconds=30)) == 0
    assert await statuses(sessions) == [("email", "pending", 1)]  # ещё рано

    moment = NOW
    for _ in range(5):
        moment += timedelta(hours=2)
        await send(sessions, {"email": email}, moment)
    assert await statuses(sessions) == [("email", "failed", 6)]


async def test_resolved_alert_is_not_sent(sessions: Sessions) -> None:
    business, _ = await owner(sessions)
    alerts = SqlAlerts(sessions, business)
    await alerts.raise_alert("platform_unavailable", {})
    await alerts.resolve("platform_unavailable")
    email = Recorder()
    await send(sessions, {"email": email})
    assert email.sent == []
    assert await statuses(sessions) == [("email", "skipped", 1)]


async def test_unconfigured_channel_waits(sessions: Sessions) -> None:
    business, _ = await owner(sessions)
    await SqlAlerts(sessions, business).raise_alert("zero_balance", {})
    assert await send(sessions, {}) == 0
    assert await statuses(sessions) == [("email", "pending", 0)]
    assert await send(sessions, {"email": Recorder()}) == 1


async def test_interrupted_sending_is_taken_again_after_lease(sessions: Sessions) -> None:
    business, _ = await owner(sessions)
    await SqlAlerts(sessions, business).raise_alert("zero_balance", {})
    outbox = SqlOutbox(sessions)
    [claimed] = await outbox.claim([Channel.EMAIL], NOW, 10)
    assert await outbox.claim([Channel.EMAIL], NOW + timedelta(minutes=1), 10) == []
    email = Recorder()
    assert await send(sessions, {"email": email}, NOW + timedelta(minutes=6)) == 1
    assert await statuses(sessions) == [("email", "sent", 2)]
    assert claimed.attempts == 1


async def test_each_owner_gets_only_own_alert(sessions: Sessions) -> None:
    first, _ = await owner(sessions, "first@example.test")
    second, _ = await owner(sessions, "second@example.test")
    await SqlAlerts(sessions, first).raise_alert("zero_balance", {})
    email = Recorder()
    await send(sessions, {"email": email})
    assert [address for address, _ in email.sent] == ["first@example.test"]
    assert second  # у второго тревоги нет — и письма тоже


async def test_telegram_link_then_alert_goes_to_chat(sessions: Sessions) -> None:
    business, user = await owner(sessions)
    links = SqlTelegramLinks(sessions)
    url = await start_telegram_link(links, user, "adhelper_bot", NOW, token=lambda: "code-1")
    assert url == "https://t.me/adhelper_bot?start=code-1"
    async with sessions() as session:
        stored = await session.scalar(text("SELECT code_hash FROM telegram_links"))
    assert stored != "code-1"  # в базе только хэш

    bot = FakeBot(
        [
            TelegramUpdate(10, 555, "/start wrong"),
            TelegramUpdate(11, None, ""),  # группа: не отвечаем
            TelegramUpdate(12, 777, "/start code-1"),
            TelegramUpdate(13, 777, "удали все кампании"),
        ]
    )
    assert await handle_telegram_updates(bot, links, NOW) == 1
    assert [a for a, _ in bot.sent] == ["555", "777", "777"]
    assert "устарела" in bot.sent[0][1].text and "Готово" in bot.sent[1][1].text
    assert await links.linked(user)
    await handle_telegram_updates(bot, links, NOW)
    assert bot.offsets == [None, 14]

    await SqlAlerts(sessions, business).raise_alert("budget_stopped", {})
    telegram = Recorder()
    await send(sessions, {"email": Recorder(), "telegram": telegram})
    assert [address for address, _ in telegram.sent] == ["777"]

    await links.unlink(user)
    assert not await links.linked(user)


async def test_expired_or_reused_link_does_not_bind(sessions: Sessions) -> None:
    _, user = await owner(sessions)
    links = SqlTelegramLinks(sessions)
    await start_telegram_link(links, user, "bot", NOW, token=lambda: "old")
    bot = FakeBot([TelegramUpdate(1, 777, "/start old")])
    await handle_telegram_updates(bot, links, NOW + timedelta(minutes=16))
    assert not await links.linked(user)
    assert "устарела" in bot.sent[0][1].text


async def test_dashboard_money_matches_cabinet(sessions: Sessions, tmp_path: Path) -> None:
    demo = await seed_demo(sessions, NOW, tmp_path, mismatch=True)
    facts = await DashboardMoney(sessions).money(demo.business_id, NOW)
    assert facts is not None
    assert facts.balance_gap == 1_240 * M
    assert facts.tolerance == 800 * M


async def test_launch_summary_is_sent_once_and_stays_out_of_problems(sessions: Sessions) -> None:
    business, _ = await owner(sessions)
    alerts = SqlAlerts(sessions, business)
    details = {"day": "2026-11-04", "headlines": ["Маникюр у дома"], "count": 1}
    assert await alerts.notice("ads_launched", "ads_launched:2026-11-04", details) is True
    # Повтор задачи за тот же день второго письма не даёт.
    assert await alerts.notice("ads_launched", "ads_launched:2026-11-04", details) is False
    # Сводка — не проблема: в «Сегодня» её нет.
    assert await alerts.open_kinds() == []
    email = Recorder()
    assert await send(sessions, {"email": email}) == 1
    [(_, message)] = email.sent
    assert "«Маникюр у дома»" in message.text
    assert "04.11.2026" in message.text
    assert "одним нажатием" in message.text
