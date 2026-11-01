"""Telegram Bot API без вебхука: бот сам забирает сообщения, публичный адрес не нужен.

Токен бота входит в адрес запроса. Поэтому ошибки наружу уходят только с типом,
а журнал httpx, который пишет адреса запросов, поднят до WARNING.
"""

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from adhelper.notifications.domain.model import Message, TelegramUpdate
from adhelper.notifications.domain.ports import DeliveryFailed

logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.telegram.org"
TIMEOUT_SECONDS = 20


class TelegramApi:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None, base: str = API):
        self._url = f"{base}/bot{token}"
        self._client = client

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            if self._client is not None:
                response = await self._client.post(f"{self._url}/{method}", json=payload)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await client.post(f"{self._url}/{method}", json=payload)
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise DeliveryFailed(f"{method}: {type(error).__name__}") from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            code = body.get("error_code") if isinstance(body, dict) else None
            raise DeliveryFailed(f"{method}: {code or response.status_code}")
        return body.get("result")

    async def send(self, address: str, message: Message) -> None:
        # Без разметки: название бизнеса и тексты не разбираются как HTML или Markdown.
        await self._call(
            "sendMessage",
            {
                "chat_id": int(address),
                "text": message.text,
                "link_preview_options": {"is_disabled": True},
            },
        )

    async def updates(self, offset: int | None) -> Sequence[TelegramUpdate]:
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        updates = []
        for item in result if isinstance(result, list) else []:
            message = item.get("message") or {}
            chat = message.get("chat") or {}
            # Привязываем только личные чаты: тревоги о деньгах не уходят в группы.
            if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
                updates.append(TelegramUpdate(item["update_id"], None, ""))
                continue
            updates.append(TelegramUpdate(item["update_id"], chat["id"], message.get("text") or ""))
        return updates
