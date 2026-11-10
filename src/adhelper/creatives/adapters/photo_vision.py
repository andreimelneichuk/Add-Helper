import base64
import json
import logging
from collections.abc import Sequence

import httpx
from pydantic import SecretStr

from adhelper.creatives.domain.photo_match import (
    SUBJECT_MAX_TOKENS,
    SUBJECT_SYSTEM,
    read_subject,
    subject_schema,
)

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 60.0


class OpenAiVision:
    """Модель, которая смотрит на фото: то же OpenAI-совместимое API, картинка в запросе.

    Возвращает значение из закрытого списка или None при любом сбое. Фото владельца и ответ
    модели — данные: сбой не мешает объявлению, оно получит другое фото или останется без него.
    """

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._transport = transport

    async def subject(self, image: bytes, services: Sequence[str]) -> str | None:
        encoded = base64.b64encode(image).decode("ascii")
        schema = json.dumps(subject_schema(services), ensure_ascii=False)
        listed = ", ".join(f"«{name}»" for name in services) or "услуг нет"
        body = {
            "model": self._model,
            "max_tokens": SUBJECT_MAX_TOKENS,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": f"{SUBJECT_SYSTEM}\nСхема ответа: {schema}"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Услуги бизнеса: {listed}."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        },
                    ],
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(self._url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                return None
            text = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            # Только тип ошибки: в тексте может оказаться ключ.
            logger.warning("photo vision failed: %s", type(error).__name__)
            return None
        return read_subject(text or "", services)
