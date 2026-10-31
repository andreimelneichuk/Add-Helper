import httpx
from pydantic import SecretStr

from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

COMPLETION_URL = "https://ai.api.cloud.yandex.net/foundationModels/v1/completion"
FINAL_STATUS = "ALTERNATIVE_STATUS_FINAL"
TIMEOUT_SECONDS = 60.0


class YandexAiStudioModel:
    """Формат сверен с документацией 2026-10-31; реальным вызовом не проверен: ключа ещё нет."""

    def __init__(
        self,
        api_key: SecretStr,
        folder: str,
        model: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._folder = folder
        self._model = model
        self._transport = transport

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        body = {
            "modelUri": f"gpt://{self._folder}/{self._model}",
            "completionOptions": {
                "stream": False,
                "temperature": 0.6,
                "maxTokens": str(max_tokens),
            },
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
            "jsonSchema": {"schema": schema},
        }
        headers = {
            "Authorization": f"Api-Key {self._api_key.get_secret_value()}",
            "x-folder-id": self._folder,
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(COMPLETION_URL, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            # В справочнике ответ без обёртки, а исторически поля лежали в `result`.
            result = data.get("result", data)
            alternative = result["alternatives"][0]
            status = alternative["status"]
            text = alternative["message"]["text"]
            tokens = int(result["usage"]["totalTokens"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            # Только тип ошибки: в тексте может оказаться ключ или содержимое запроса.
            raise ModelUnavailable(type(error).__name__) from None
        if status != FINAL_STATUS:
            raise ModelUnavailable(status)
        return ModelReply(text, tokens)
