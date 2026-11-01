"""Адаптеры каналов на подставном транспорте: формат запросов и ничего секретного в ошибках."""

import json
import smtplib
from typing import Any, ClassVar

import httpx
import pytest

from adhelper.notifications.adapters.smtp import SmtpSender
from adhelper.notifications.adapters.telegram import TelegramApi
from adhelper.notifications.domain.model import Message
from adhelper.notifications.domain.ports import DeliveryFailed

TOKEN = "123456:SECRET-TOKEN"
MESSAGE = Message("Счёт не сходится — Студия\n«Лак»", "Текст <b>без</b> разметки")


def telegram(handler: Any) -> TelegramApi:
    return TelegramApi(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_telegram_sends_plain_text() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    await telegram(handler).send("777", MESSAGE)
    [request] = seen
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == 777 and body["text"] == MESSAGE.text
    assert "parse_mode" not in body


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(403, json={"ok": False, "error_code": 403, "description": TOKEN}),
        lambda r: httpx.Response(502, text="bad gateway"),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"https://api.telegram.org/bot{TOKEN}")),
    ],
)
async def test_telegram_errors_never_carry_token(handler: Any) -> None:
    with pytest.raises(DeliveryFailed) as caught:
        await telegram(handler).send("777", MESSAGE)
    assert "SECRET" not in str(caught.value)
    error = caught.value
    # Исходная ошибка httpx с адресом запроса не попадает в цепочку трассировки.
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)


async def test_telegram_updates_keep_only_private_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["offset"] == 5
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 5,
                        "message": {"chat": {"id": 7, "type": "private"}, "text": "/start x"},
                    },
                    {
                        "update_id": 6,
                        "message": {"chat": {"id": -9, "type": "group"}, "text": "/start y"},
                    },
                    {"update_id": 7, "message": {"chat": {"id": 7, "type": "private"}}},
                ],
            },
        )

    updates = await telegram(handler).updates(5)
    assert [(u.update_id, u.chat_id, u.text) for u in updates] == [
        (5, 7, "/start x"),
        (6, None, ""),
        (7, 7, ""),
    ]


class FakeSmtp:
    instances: ClassVar[list["FakeSmtp"]] = []

    def __init__(self, host: str, port: int, timeout: int, fail: bool = False) -> None:
        self.calls: list[Any] = [("connect", host, port)]
        self.fail = fail
        FakeSmtp.instances.append(self)

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: Any) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def send_message(self, email: Any) -> None:
        if self.fail:
            raise smtplib.SMTPRecipientsRefused({"anna@example.test": (550, b"no such user")})
        self.calls.append(("send", email["To"], email["Subject"], email.get_content()))


async def test_smtp_sends_plain_letter_with_safe_subject() -> None:
    FakeSmtp.instances.clear()
    sender = SmtpSender("smtp.example", 587, "alerts@example", "alerts", "pw", True, FakeSmtp)
    await sender.send("anna@example.test", MESSAGE)
    [smtp] = FakeSmtp.instances
    assert smtp.calls[:3] == [("connect", "smtp.example", 587), "starttls", ("login", "alerts")]
    _, to, subject, content = smtp.calls[3]
    assert to == "anna@example.test"
    assert subject == "Счёт не сходится — Студия «Лак»"
    assert content.strip() == MESSAGE.text


async def test_smtp_error_carries_only_type() -> None:
    def failing(host: str, port: int, timeout: int) -> FakeSmtp:
        return FakeSmtp(host, port, timeout, fail=True)

    sender = SmtpSender("smtp.example", 465, "alerts@example", connect=failing)
    with pytest.raises(DeliveryFailed) as caught:
        await sender.send("anna@example.test", MESSAGE)
    assert str(caught.value) == "SMTPRecipientsRefused"
