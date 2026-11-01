"""Каналы тревог через настоящий HTTP-слой: статус, ссылка на бота, отвязка, CSRF."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from adhelper.notifications.adapters.sql import SqlTelegramLinks
from adhelper.notifications.application.service import handle_telegram_updates
from adhelper.notifications.domain.model import Message, TelegramUpdate
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import ANNA, Browser, _login
from tests.api.test_cabinet import Sessions

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


class Bot:
    def __init__(self, code: str) -> None:
        self.code = code
        self.sent: list[str] = []

    async def updates(self, offset: int | None) -> list[TelegramUpdate]:
        return [TelegramUpdate(1, 4242, f"/start {self.code}")]

    async def send(self, address: str, message: Message) -> None:
        self.sent.append(address)


def _app(client: httpx.AsyncClient) -> object:
    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    return transport.app


async def test_channels_link_and_unlink(
    browser: Browser, provider: FakeProvider, sessions: Sessions
) -> None:
    client = browser()
    csrf = await _login(client, provider, ANNA)
    app = _app(client)
    app.state.clock = lambda: NOW  # type: ignore[attr-defined]

    status = await client.get("/api/me/notifications")
    assert status.json() == {
        "email": "anna@example.test",
        "telegram": {"available": False, "linked": False},
    }
    assert (
        await client.post("/api/me/telegram", headers={"X-CSRF-Token": csrf})
    ).status_code == 503

    app.state.telegram_bot = "adhelper_bot"  # type: ignore[attr-defined]
    assert (await client.post("/api/me/telegram")).status_code == 403
    link = await client.post("/api/me/telegram", headers={"X-CSRF-Token": csrf})
    url = urlsplit(link.json()["url"])
    assert (url.netloc, url.path) == ("t.me", "/adhelper_bot")
    code = url.query.removeprefix("start=")

    await handle_telegram_updates(Bot(code), SqlTelegramLinks(sessions), NOW)
    status = await client.get("/api/me/notifications")
    assert status.json()["telegram"] == {"available": True, "linked": True}

    gone = await client.delete("/api/me/telegram", headers={"X-CSRF-Token": csrf})
    assert gone.status_code == 204
    assert (await client.get("/api/me/notifications")).json()["telegram"]["linked"] is False


async def test_anonymous_gets_401(browser: Browser) -> None:
    client = browser()
    assert (await client.get("/api/me/notifications")).status_code == 401
