import re
from collections.abc import Sequence

from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeRules,
    Violation,
    ViolationKind,
)

BASIS_POINTS = 10000
NUMBER = re.compile(r"\d[\d\s ]*\d|\d")
WORD = re.compile(r"[a-zа-яё0-9№]+", re.IGNORECASE)
LATIN = re.compile(r"[a-z]{2,}", re.IGNORECASE)


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\D", "", match) for match in NUMBER.findall(text)}


def profile_facts(profile: BusinessProfile) -> dict[str, object]:
    """Поля профиля для модели и судьи. Только то, что можно показать в рекламе."""
    return {
        "название": profile.name,
        "услуги": [{"услуга": s.name, "цена_руб": s.price} for s in profile.services],
        "адрес": profile.address,
        "особенности": list(profile.features),
    }


def profile_text(profile: BusinessProfile) -> str:
    parts = [profile.name, profile.address, *profile.features]
    for service in profile.services:
        parts.append(service.name)
        if service.price is not None:
            parts.append(str(service.price))
    return " ".join(parts)


def _visible_length(text: str, rules: CreativeRules) -> int:
    """Узкие символы площадка считает отдельно: сверх их квоты они идут в общий счёт."""
    narrow = sum(1 for char in text if char in rules.narrow_chars)
    return len(text) - min(narrow, rules.narrow_max)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![а-яёa-z0-9]){re.escape(phrase)}(?![а-яёa-z0-9])", text) is not None


def _has_stem(text: str, stem: str) -> bool:
    return any(word.startswith(stem) for word in WORD.findall(text))


def check_draft(
    draft: AdDraft, profile: BusinessProfile, rules: CreativeRules
) -> tuple[Violation, ...]:
    """Детерминированная проверка: ответ модели — данные, а не утверждённый текст."""
    found: list[Violation] = []
    if not draft.headline.strip() or not draft.text.strip():
        found.append(Violation(ViolationKind.EMPTY, "заголовок или текст"))
    if len(draft.headline) > rules.headline_max:
        found.append(Violation(ViolationKind.TOO_LONG, f"заголовок {len(draft.headline)}"))
    if _visible_length(draft.text, rules) > rules.text_max:
        found.append(Violation(ViolationKind.TOO_LONG, f"текст {len(draft.text)}"))

    full = f"{draft.headline} {draft.text}"
    lowered = full.lower()
    source = profile_text(profile)
    source_lowered = source.lower()
    known_numbers = _numbers(source)
    for number in sorted(_numbers(full) - known_numbers):
        found.append(Violation(ViolationKind.UNCONFIRMED_NUMBER, number))
    for word in rules.forbidden_words:
        if _contains_phrase(lowered, word) and not _contains_phrase(source_lowered, word):
            found.append(Violation(ViolationKind.FORBIDDEN_WORD, word))
    for word in rules.fact_words:
        if _contains_phrase(lowered, word) and not _contains_phrase(source_lowered, word):
            found.append(Violation(ViolationKind.UNCONFIRMED_CLAIM, word))
    for stem in rules.fact_stems:
        if _has_stem(lowered, stem) and not _has_stem(source_lowered, stem):
            found.append(Violation(ViolationKind.UNCONFIRMED_CLAIM, stem))
    known_latin = {word.lower() for word in LATIN.findall(source)}
    for word in sorted({word.lower() for word in LATIN.findall(full)} - known_latin):
        found.append(Violation(ViolationKind.FOREIGN_WORD, word))
    return tuple(found)


def similarity_bp(first: AdDraft, second: AdDraft) -> int:
    """Доля общих слов, целое число базисных пунктов."""
    words_a = {word.lower() for word in WORD.findall(f"{first.headline} {first.text}")}
    words_b = {word.lower() for word in WORD.findall(f"{second.headline} {second.text}")}
    union = words_a | words_b
    return len(words_a & words_b) * BASIS_POINTS // len(union) if union else BASIS_POINTS


def too_similar_to_any(draft: AdDraft, kept: Sequence[AdDraft], rules: CreativeRules) -> bool:
    return any(similarity_bp(draft, other) > rules.similarity_max_bp for other in kept)


def template_drafts(profile: BusinessProfile, rules: CreativeRules) -> tuple[AdDraft, ...]:
    """Запасной набор без модели: только поля профиля, и каждый вариант проходит ту же проверку."""
    candidates: list[AdDraft] = []
    # Имя бизнеса бывает пустым (профиль заполнен вручную): пустых кавычек в рекламе не нужно.
    brand = f"«{profile.name.strip()}»" if profile.name.strip() else ""
    for service in profile.services:
        place = f"Ждём вас: {profile.address}." if profile.address else "Запишитесь онлайн."
        candidates.append(AdDraft(f"{service.name} в {brand}" if brand else service.name, place))
        if service.price is not None:
            candidates.append(
                AdDraft(
                    f"{service.name} за {service.price} ₽", f"{brand}. {place}" if brand else place
                )
            )
        for feature in profile.features:
            candidates.append(
                AdDraft(
                    service.name, f"{feature}. {brand}. {place}" if brand else f"{feature}. {place}"
                )
            )
    kept: list[AdDraft] = []
    for candidate in candidates:
        if check_draft(candidate, profile, rules):
            continue
        if not too_similar_to_any(candidate, kept, rules):
            kept.append(candidate)
    return tuple(kept)
