"""Поддельный писатель указывает факты к числам сам, как настоящий по SYSTEM_PROMPT.

Тестам сценариев важно другое поведение, поэтому ссылки добавляются по блоку ФАКТЫ из запроса:
к каждому числу объявления — первый факт, где это число есть. Объявление с явным `claims`
не трогаем: так тесты проверяют и неверные ссылки.
"""

import json
import re

NUMBER = re.compile(r"\d[\d\s ]*\d|\d")


def _digits(text: str) -> set[str]:
    return {re.sub(r"\D", "", match) for match in NUMBER.findall(text)}


def with_claims(reply: str, user: str) -> str:
    if "ФАКТЫ:\n" not in user:
        return reply
    try:
        data = json.loads(reply)
    except ValueError:
        return reply
    if not isinstance(data, dict) or not isinstance(data.get("ads"), list):
        return reply
    facts: dict[str, str] = json.loads(user.rsplit("ФАКТЫ:\n", 1)[1])
    for ad in data["ads"]:
        if not isinstance(ad, dict) or "claims" in ad:
            continue
        claims = []
        for match in NUMBER.findall(f"{ad.get('headline', '')} {ad.get('text', '')}"):
            digits = re.sub(r"\D", "", match)
            key = next((k for k, value in facts.items() if digits in _digits(value)), None)
            if key is not None:
                claims.append({"fragment": match.strip(), "fact_key": key})
        ad["claims"] = claims
    return json.dumps(data, ensure_ascii=False)
