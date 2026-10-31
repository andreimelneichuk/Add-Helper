"""Адаптер на подставном транспорте. Формат сверен с документацией, реального вызова не было."""

import json

import httpx
import pytest
from pydantic import SecretStr

from adhelper.creatives.adapters.factory import build_text_model
from adhelper.creatives.adapters.yandex import COMPLETION_URL, YandexAiStudioModel
from adhelper.creatives.domain.model import ModelUnavailable

KEY = SecretStr("secret-key")


def reply(status: str = "ALTERNATIVE_STATUS_FINAL", wrap: bool = False) -> dict[str, object]:
    body: dict[str, object] = {
        "alternatives": [
            {"message": {"role": "assistant", "text": '{"ads":[]}'}, "status": status}
        ],
        "usage": {"inputTextTokens": "10", "completionTokens": "5", "totalTokens": "15"},
        "modelVersion": "1",
    }
    return {"result": body} if wrap else body


def model(handler: httpx.MockTransport) -> YandexAiStudioModel:
    return YandexAiStudioModel(KEY, "folder", "yandexgpt-5.1", handler)


async def call(subject: YandexAiStudioModel) -> tuple[str, int]:
    result = await subject.complete_json("сис", "поль", {"type": "object"}, 100)
    return result.text, result.tokens


async def test_request_follows_documented_format() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply())

    assert await call(model(httpx.MockTransport(handler))) == ('{"ads":[]}', 15)
    request = seen[0]
    assert str(request.url) == COMPLETION_URL
    assert request.headers["authorization"] == "Api-Key secret-key"
    body = json.loads(request.content)
    assert body["modelUri"] == "gpt://folder/yandexgpt-5.1"
    assert body["completionOptions"]["maxTokens"] == "100"
    assert body["jsonSchema"] == {"schema": {"type": "object"}}
    assert [message["role"] for message in body["messages"]] == ["system", "user"]


async def test_wrapped_reply_is_read_too() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=reply(wrap=True)))
    assert (await call(model(transport)))[1] == 15


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(429, json={}),
        httpx.Response(200, text="не json"),
        httpx.Response(200, json={"alternatives": []}),
        httpx.Response(200, json=reply("ALTERNATIVE_STATUS_TRUNCATED_FINAL")),
        httpx.Response(200, json=reply("ALTERNATIVE_STATUS_CONTENT_FILTER")),
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


def test_provider_is_chosen_by_configuration() -> None:
    assert isinstance(build_text_model("yandex", KEY, "f", "m"), YandexAiStudioModel)
    assert build_text_model("none", KEY, "f", "m") is None
    assert build_text_model("yandex", None, "f", "m") is None
    assert build_text_model("yandex", SecretStr(""), "f", "m") is None
    assert build_text_model("yandex", KEY, "", "m") is None
    with pytest.raises(ValueError, match="провайдер"):
        build_text_model("other", KEY, "f", "m")
