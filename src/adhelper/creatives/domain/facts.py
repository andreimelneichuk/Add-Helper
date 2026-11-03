"""Факты профиля под короткими ключами и проверка ссылок писателя на них (docs/12-agents.md §4).

Писатель к каждому утверждению о бизнесе указывает фрагмент и ключ факта. Код проверяет, что
ключ есть, фрагмент взят из объявления, а каждое число объявления стоит во фрагменте, ключ
которого содержит это число. Смысл фрагмента код не понимает: это работа судьи.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from adhelper.creatives.domain.model import AdDraft, BusinessProfile, Violation, ViolationKind

NUMBER = re.compile(r"\d[\d\s ]*\d|\d")


@dataclass(frozen=True)
class Claim:
    fragment: str
    fact_key: str


def keyed_facts(profile: BusinessProfile) -> dict[str, str]:
    """Ключи стабильны для одного профиля: name, address, s1, s1_price, f1…"""
    facts = {"name": profile.name}
    if profile.address:
        facts["address"] = profile.address
    for number, service in enumerate(profile.services, 1):
        facts[f"s{number}"] = service.name
        if service.price is not None:
            facts[f"s{number}_price"] = f"{service.name}: {service.price} ₽"
    for number, feature in enumerate(profile.features, 1):
        facts[f"f{number}"] = feature
    return facts


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\D", "", match) for match in NUMBER.findall(text)}


def _squeeze(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def check_claims(
    draft: AdDraft, claims: Sequence[Claim], facts: dict[str, str]
) -> tuple[Violation, ...]:
    found: list[Violation] = []
    full = _squeeze(f"{draft.headline} {draft.text}")
    bound: set[str] = set()
    for claim in claims:
        if claim.fact_key not in facts:
            found.append(Violation(ViolationKind.UNBOUND_CLAIM, f"нет ключа {claim.fact_key}"))
            continue
        fragment = _squeeze(claim.fragment)
        if not fragment or fragment not in full:
            found.append(Violation(ViolationKind.UNBOUND_CLAIM, f"нет фрагмента {claim.fragment}"))
            continue
        # Число засчитывается, только если оно есть и во фрагменте, и в факте по ключу:
        # так цена одной услуги не уходит в объявление о другой.
        bound |= _numbers(claim.fragment) & _numbers(facts[claim.fact_key])
    for number in sorted(_numbers(f"{draft.headline} {draft.text}") - bound):
        found.append(Violation(ViolationKind.UNBOUND_CLAIM, f"число {number} без факта"))
    return tuple(found)
