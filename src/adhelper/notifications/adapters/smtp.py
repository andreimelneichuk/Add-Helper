import asyncio
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from adhelper.notifications.domain.model import Message
from adhelper.notifications.domain.ports import DeliveryFailed

TIMEOUT_SECONDS = 20


def _header(value: str) -> str:
    # Название бизнеса вводит владелец: перевод строки в заголовке письма недопустим.
    return " ".join(value.split())


class SmtpSender:
    """Любой SMTP-сервер: Яндекс 360, Unisender, свой. Параметры только из конфигурации."""

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        username: str = "",
        password: str = "",
        starttls: bool = False,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls
        self._connect = connect or (smtplib.SMTP if starttls else smtplib.SMTP_SSL)

    async def send(self, address: str, message: Message) -> None:
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = address
        email["Subject"] = _header(message.subject)
        email.set_content(message.text)
        try:
            await asyncio.to_thread(self._deliver, email)
        except (OSError, smtplib.SMTPException) as error:
            # Текст ответа сервера может содержать адрес: наружу только тип.
            raise DeliveryFailed(type(error).__name__) from None

    def _deliver(self, email: EmailMessage) -> None:
        with self._connect(self._host, self._port, timeout=TIMEOUT_SECONDS) as smtp:
            if self._starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(email)
