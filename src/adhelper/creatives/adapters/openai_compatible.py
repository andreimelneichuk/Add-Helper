import json
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

TIMEOUT_SECONDS = 60.0
# Ответ считаем пригодным, только если модель закончила сама: обрыв по длине рвёт JSON.
FINAL_REASONS = frozenset({"stop", "end_turn"})


@dataclass(frozen=True)
class Usage:
    """Расход одного ответа. `cost_usd` даёт OpenRouter, у других провайдеров его нет."""

    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None


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
        on_usage: Callable[[Usage], None] | None = None,
        temperature: float = 0,
        no_reasoning: bool = False,
    ) -> None:
        self._temperature = temperature
        self._no_reasoning = no_reasoning
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._project = project
        self._transport = transport
        self._on_usage = on_usage

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
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._no_reasoning:
            # Писателю рассуждение не нужно, а исписанные им токены стоят денег и срывают ответ.
            # Параметр OpenRouter; модели без рассуждения его игнорируют.
            body["reasoning"] = {"enabled": False}
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
            usage = data["usage"]
            spent = Usage(
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
                float(usage["cost"]) if usage.get("cost") is not None else None,
            )
            tokens = int(usage.get("total_tokens", spent.prompt_tokens + spent.completion_tokens))
            # Учёт до проверок ответа: обрезанный или пустой ответ тоже оплачен.
            if self._on_usage is not None:
                self._on_usage(spent)
            choice = data["choices"][0]
            reason = choice["finish_reason"]
            text = choice["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            # Только тип ошибки: в тексте может оказаться ключ или содержимое запроса.
            raise ModelUnavailable(type(error).__name__) from None
        if reason not in FINAL_REASONS:
            raise ModelUnavailable(reason, tokens)
        if not text:
            raise ModelUnavailable("empty", tokens)
        return ModelReply(text, tokens)
