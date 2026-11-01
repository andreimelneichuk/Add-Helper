import json

import httpx
from pydantic import SecretStr

from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

TIMEOUT_SECONDS = 60.0
# Ответ считаем пригодным, только если модель закончила сама: обрыв по длине рвёт JSON.
FINAL_REASONS = frozenset({"stop", "end_turn"})


class OpenAiCompatibleModel:
    """Один клиент Chat Completions для любого провайдера с OpenAI-совместимым API.

    Провайдер задаётся адресом, ключом и именем модели. Формат сверен реальным вызовом
    OpenRouter 2026-11-01; Yandex AI Studio сверен по документации, вызова не было.
    """

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str,
        project: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._project = project
        self._transport = transport

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        # `json_schema` в response_format поддерживают не все провайдеры, поэтому схема идёт
        # текстом: без неё модель придумывает свои ключи и ответ не разбирается.
        schema_text = json.dumps(schema, ensure_ascii=False)
        system = f"{system}\nСхема ответа (JSON Schema): {schema_text}"
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        # Так OpenAI SDK передаёт `project`; Yandex AI Studio ждёт здесь каталог.
        if self._project:
            headers["OpenAI-Project"] = self._project
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(self._url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            # Некоторые шлюзы возвращают ошибку провайдера под кодом 200, в поле error.
            if data.get("error"):
                raise ModelUnavailable("provider_error")
            choice = data["choices"][0]
            reason = choice["finish_reason"]
            text = choice["message"]["content"]
            tokens = int(data["usage"]["total_tokens"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            # Только тип ошибки: в тексте может оказаться ключ или содержимое запроса.
            raise ModelUnavailable(type(error).__name__) from None
        if reason not in FINAL_REASONS:
            raise ModelUnavailable(reason)
        if not text:
            raise ModelUnavailable("empty")
        return ModelReply(text, tokens)
