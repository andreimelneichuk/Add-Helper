"""Факты профиля под короткими ключами и проверка ссылок писателя на них (docs/12-agents.md §4).

Писатель к каждому утверждению о бизнесе указывает фрагмент и ключ факта. Код проверяет, что
ключ есть, фрагмент взят из объявления, а каждое число объявления стоит во фрагменте, ключ
которого содержит это число.

С правилами creatives-3 добавляется рамка. Во фрагменте-факте только слова его факта (окончания
могут отличаться) и служебные слова из правил. Факт с числом пишется целиком, без потери
условия. Вне фрагментов-фактов нет слов о графике, выезде, сроках и условиях. Смысл фраз без
фактов (призыв, мысль о клиенте) код не понимает: это работа судьи.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeRules,
    Violation,
    ViolationKind,
)

NUMBER = re.compile(r"\d[\d\s ]*\d|\d")
TOKEN = re.compile(r"\d[\d\s ]*\d|\d|[a-zа-я]+")
# Короткие слова сокращений: «ул» в факте узнаёт «улица», «пр» — «проспект».
ABBREVIATION_MAX = 3


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


def _tokens(text: str) -> list[str]:
    """Слова и числа по порядку; в числе только цифры: «1 500» и «1500» совпадают."""
    return [
        re.sub(r"\D", "", token) if token[0].isdigit() else token
        for token in TOKEN.findall(text.lower().replace("ё", "е"))
    ]


def _same_word(word: str, fact_word: str) -> bool:
    if word == fact_word:
        return True
    if word.isdigit() or fact_word.isdigit():
        return False
    shorter, longer = sorted((word, fact_word), key=len)
    if len(shorter) <= ABBREVIATION_MAX:
        return len(shorter) >= 2 and longer.startswith(shorter)
    common = len(_common_prefix(word, fact_word))
    # Разница только в окончании: «стерильные» и «стерильными», «будни» и «будням».
    return common >= 4 and common >= len(longer) - 3


def _common_prefix(left: str, right: str) -> str:
    size = 0
    while size < min(len(left), len(right)) and left[size] == right[size]:
        size += 1
    return left[:size]


def _frame(claim: Claim, fact: str, rules: CreativeRules) -> list[Violation]:
    glue = set(rules.fact_glue)
    words = [t for t in _tokens(claim.fragment) if t not in glue]
    fact_words = [t for t in _tokens(fact) if t not in glue]
    found = [
        Violation(ViolationKind.UNBOUND_CLAIM, f"слово «{word}» не из факта {claim.fact_key}")
        for word in words
        if not any(_same_word(word, fact_word) for fact_word in fact_words)
    ]
    # Число тянет за собой весь факт: «скидка 10% по будням» без «до 12» — другое условие.
    if _numbers(claim.fragment) & _numbers(fact):
        missing = [w for w in fact_words if not any(_same_word(t, w) for t in words)]
        if missing:
            listed = ", ".join(f"«{w}»" for w in missing)
            found.append(
                Violation(
                    ViolationKind.UNBOUND_CLAIM, f"в факте {claim.fact_key} не хватает {listed}"
                )
            )
    return found


def _free_text(full: str, fragments: Sequence[str], rules: CreativeRules) -> list[Violation]:
    """Текст вне фрагментов-фактов не говорит о графике, выезде, сроках и условиях."""
    rest = full
    for fragment in fragments:
        rest = rest.replace(fragment, " ", 1)
    words = [t for t in _tokens(rest) if not t.isdigit()]
    phrases = [stem for stem in rules.claim_stems if " " in stem]
    stems = [stem for stem in rules.claim_stems if " " not in stem]
    found = [
        Violation(ViolationKind.UNBOUND_CLAIM, f"«{word}» без факта")
        for word in words
        if any(word.startswith(stem) for stem in stems)
    ]
    squeezed = " ".join(words)
    found.extend(
        Violation(ViolationKind.UNBOUND_CLAIM, f"«{phrase}» без факта")
        for phrase in phrases
        if f" {phrase} " in f" {squeezed} "
    )
    return found


def check_claims(
    draft: AdDraft,
    claims: Sequence[Claim],
    facts: dict[str, str],
    rules: CreativeRules | None = None,
) -> tuple[Violation, ...]:
    found: list[Violation] = []
    framed = rules is not None and bool(rules.fact_glue)
    kept: list[str] = []
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
        if framed:
            assert rules is not None
            found.extend(_frame(claim, facts[claim.fact_key], rules))
            kept.append(fragment)
    if framed:
        assert rules is not None
        found.extend(_free_text(full, kept, rules))
    for number in sorted(_numbers(f"{draft.headline} {draft.text}") - bound):
        found.append(Violation(ViolationKind.UNBOUND_CLAIM, f"число {number} без факта"))
    return tuple(found)
