"""Поддельный писатель указывает факты к числам сам, как настоящий по SYSTEM_PROMPT.

Тестам сценариев важно другое поведение, поэтому ссылки добавляются по блоку ФАКТЫ из запроса,
как требует рамка creatives-3: факт, который стоит в объявлении дословно, — фрагментом целиком;
число вне таких фрагментов — вместе с заголовком или предложением, где оно стоит, к первому
факту с этим числом. Объявление с явным `claims` не трогаем: так тесты проверяют и неверные
ссылки.
"""

import json
import re

NUMBER = re.compile(r"\d[\d\s ]*\d|\d")


def _digits(text: str) -> set[str]:
    return {re.sub(r"\D", "", match) for match in NUMBER.findall(text)}


def _parts(headline: str, text: str) -> list[str]:
    """Заголовок и предложения текста без конечной точки."""
    sentences = [p.strip().rstrip(".!") for p in re.split(r"(?<=[.!?])\s+", text)]
    return [headline, *(p for p in sentences if p)]


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
        whole = f"{ad.get('headline', '')} {ad.get('text', '')}"
        claims = [
            {"fragment": value, "fact_key": key}
            for key, value in facts.items()
            if value and value.lower() in whole.lower()
        ]
        verbatim = [c["fragment"].lower() for c in claims]
        for part in _parts(str(ad.get("headline", "")), str(ad.get("text", ""))):
            for match in NUMBER.findall(part):
                digits = re.sub(r"\D", "", match)
                # Число уже стоит в дословном факте этой фразы.
                if any(v in part.lower() and digits in _digits(v) for v in verbatim):
                    continue
                key = next((k for k, value in facts.items() if digits in _digits(value)), None)
                if key is not None:
                    claims.append({"fragment": part, "fact_key": key})
                    break
        ad["claims"] = claims
    return json.dumps(data, ensure_ascii=False)
