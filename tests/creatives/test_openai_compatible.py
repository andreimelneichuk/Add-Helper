"""Один клиент для любого OpenAI-совместимого API, на подставном транспорте.

Формат ответа записан с реального вызова OpenRouter 2026-11-01, ключ удалён.
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from adhelper.creatives.adapters.factory import build_text_model
from adhelper.creatives.adapters.openai_compatible import OpenAiCompatibleModel, Usage
from adhelper.creatives.domain.model import ModelUnavailable

KEY = SecretStr("secret-key")
OPENROUTER = "https://openrouter.ai/api/v1"
YANDEX = "https://ai.api.cloud.yandex.net/v1"


def reply(reason: str = "stop", content: str | None = '{"ads":[]}') -> dict[str, object]:
    message = {"role": "assistant", "content": content}
    return {
        "choices": [{"finish_reason": reason, "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def model(
    handler: httpx.MockTransport, base_url: str = OPENROUTER, project: str = ""
) -> OpenAiCompatibleModel:
    return OpenAiCompatibleModel(base_url, KEY, "some/model", project, handler)


async def call(subject: OpenAiCompatibleModel) -> tuple[str, int]:
    result = await subject.complete_json("сис", "поль", {"type": "object"}, 100)
    return result.text, result.tokens


async def test_request_follows_chat_completions_format() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply())

    assert await call(model(httpx.MockTransport(handler))) == ('{"ads":[]}', 15)
    request = seen[0]
    assert str(request.url) == f"{OPENROUTER}/chat/completions"
    assert request.headers["authorization"] == "Bearer secret-key"
    assert "openai-project" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "some/model"
    assert body["max_tokens"] == 100
    assert body["response_format"] == {"type": "json_object"}
    assert [message["role"] for message in body["messages"]] == ["system", "user"]


async def test_schema_reaches_the_model() -> None:
    """Без схемы модель придумывает свои ключи («объявления», «заголовок»), найдено замером."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply())

    schema: dict[str, object] = {
        "type": "object",
        "properties": {"ads": {"type": "array"}},
        "required": ["ads"],
    }
    await model(httpx.MockTransport(handler)).complete_json("сис", "поль", schema, 100)
    system = json.loads(seen[0].content)["messages"][0]["content"]
    assert system.startswith("сис")
    assert json.dumps(schema, ensure_ascii=False) in system


async def test_project_goes_to_header_for_yandex() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply())

    await call(model(httpx.MockTransport(handler), f"{YANDEX}/", project="b1folder"))
    assert str(seen[0].url) == f"{YANDEX}/chat/completions"
    assert seen[0].headers["openai-project"] == "b1folder"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(429, json={}),
        httpx.Response(200, text="не json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"error": {"message": "нет средств"}}),
        httpx.Response(200, json=reply(reason="length")),
        httpx.Response(200, json=reply(content=None)),
        httpx.Response(200, json=reply(content="")),
    ],
)
async def test_bad_reply_becomes_model_unavailable(response: httpx.Response) -> None:
    transport = httpx.MockTransport(lambda _: response)
    with pytest.raises(ModelUnavailable):
        await call(model(transport))


async def test_network_error_hides_details() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-key")

    with pytest.raises(ModelUnavailable) as error:
        await call(model(httpx.MockTransport(handler)))
    assert "secret-key" not in str(error.value)


def test_provider_is_chosen_by_address() -> None:
    assert isinstance(build_text_model(OPENROUTER, KEY, "m"), OpenAiCompatibleModel)
    assert isinstance(build_text_model(YANDEX, KEY, "m", "folder"), OpenAiCompatibleModel)
    assert build_text_model(YANDEX, None, "m") is None
    assert build_text_model(YANDEX, SecretStr(""), "m") is None
    assert build_text_model(YANDEX, KEY, "") is None
    with pytest.raises(ValueError, match="https"):
        build_text_model("openrouter", KEY, "m")


async def test_usage_is_reported_even_when_reply_is_cut() -> None:
    """Обрезанный ответ тоже оплачен: учёт расхода видит его, а ошибка несёт токены."""
    seen: list[Usage] = []
    cut = reply(reason="length")
    cut["usage"] = {"prompt_tokens": 10, "completion_tokens": 90, "cost": 0.0004}
    subject = OpenAiCompatibleModel(
        OPENROUTER,
        KEY,
        "some/model",
        "",
        httpx.MockTransport(lambda _: httpx.Response(200, json=cut)),
        on_usage=seen.append,
    )
    with pytest.raises(ModelUnavailable) as error:
        await call(subject)
    assert error.value.tokens == 100
    assert [(u.prompt_tokens, u.completion_tokens, u.cost_usd) for u in seen] == [(10, 90, 0.0004)]


async def test_usage_without_cost_field() -> None:
    seen: list[Usage] = []
    subject = OpenAiCompatibleModel(
        OPENROUTER,
        KEY,
        "some/model",
        "",
        httpx.MockTransport(lambda _: httpx.Response(200, json=reply())),
        on_usage=seen.append,
    )
    await call(subject)
    assert [(u.prompt_tokens, u.completion_tokens, u.cost_usd) for u in seen] == [(10, 5, None)]
