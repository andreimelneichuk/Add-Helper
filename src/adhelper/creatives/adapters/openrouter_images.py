import base64
import binascii
from collections.abc import Callable

import httpx
from pydantic import SecretStr

from adhelper.creatives.domain.images import GeneratedImage, ImageUnavailable

# Генерация идёт 10–40 с; дольше — считаем, что генератор не ответил.
TIMEOUT_SECONDS = 120.0


class OpenRouterImages:
    """Генерация картинок OpenRouter: `POST /api/v1/images`.

    Формат сверен с документацией 2026-11-05: тело `model`, `prompt`,
    `aspect_ratio`; ответ `data[].b64_json` и `media_type`, цена в `usage.cost`. Неудачная
    генерация не оплачивается и приходит кодом 502.
    """

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str,
        transport: httpx.AsyncBaseTransport | None = None,
        on_cost: Callable[[int | None], None] | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/images"
        self._api_key = api_key
        self._model = model
        self._transport = transport
        self._on_cost = on_cost

    @property
    def name(self) -> str:
        return self._model

    async def generate(self, prompt: str, aspect_ratio: str) -> GeneratedImage:
        body = {"model": self._model, "prompt": prompt, "aspect_ratio": aspect_ratio, "n": 1}
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(self._url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                raise ImageUnavailable("provider_error")
            usage = data.get("usage") or {}
            cost = usage.get("cost")
            micro = round(float(cost) * 1_000_000) if cost is not None else None
            # Учёт до разбора картинки: готовая генерация оплачена целиком.
            if self._on_cost is not None:
                self._on_cost(micro)
            item = data["data"][0]
            raw = base64.b64decode(item["b64_json"], validate=True)
            media_type = str(item.get("media_type") or "")
        except ImageUnavailable:
            raise
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            binascii.Error,
        ) as error:
            # Только тип ошибки: в тексте может оказаться ключ или запрос.
            raise ImageUnavailable(type(error).__name__) from None
        if not raw:
            raise ImageUnavailable("empty")
        if media_type == "image/svg+xml":
            # Векторные модели Recraft: в Директ SVG не загрузить.
            raise ImageUnavailable("svg")
        return GeneratedImage(raw, media_type, micro)


def build_image_model(
    base_url: str, api_key: SecretStr | None, model: str
) -> OpenRouterImages | None:
    """Без ключа или модели генерации нет: объявления идут со своими фото или без картинки."""
    if api_key is None or not api_key.get_secret_value() or not model:
        return None
    return OpenRouterImages(base_url, api_key, model)
