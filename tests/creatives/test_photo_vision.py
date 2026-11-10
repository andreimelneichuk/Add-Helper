"""Клиент, который смотрит на фото, на подставном транспорте."""

import json

import httpx
from pydantic import SecretStr

from adhelper.creatives.adapters.photo_vision import OpenAiVision

KEY = SecretStr("secret-key")


def vision(handler: httpx.MockTransport) -> OpenAiVision:
    return OpenAiVision("https://openrouter.ai/api/v1", KEY, "some/vision", handler)


def answer(content: str) -> dict[str, object]:
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


async def test_photo_goes_in_the_request_and_answer_is_checked_against_the_list() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=answer('{"subject": "Маникюр"}'))

    subject = await vision(httpx.MockTransport(handler)).subject(
        b"\xff\xd8jpeg", ["Маникюр", "Стрижка"]
    )

    assert subject == "Маникюр"
    body = seen["body"]
    assert isinstance(body, dict)
    parts = body["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "«Маникюр», «Стрижка»" in parts[0]["text"]
    assert seen["auth"] == "Bearer secret-key"


async def test_any_failure_or_invented_answer_gives_no_decision() -> None:
    cases = [
        httpx.Response(200, json=answer('{"subject": "Татуировка"}')),
        httpx.Response(200, json=answer("не JSON")),
        httpx.Response(200, json={"error": {"message": "provider"}}),
        httpx.Response(500),
    ]
    for response in cases:
        client = vision(httpx.MockTransport(lambda _r, r=response: r))
        assert await client.subject(b"x", ["Маникюр"]) is None
