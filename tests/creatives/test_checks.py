"""Проверка фактов и лимитов"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.domain.checks import (
    check_draft,
    similarity_bp,
    template_drafts,
    too_similar_to_any,
)
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, Service, ViolationKind

RULES = load_rules("creatives-1")
PROFILE = BusinessProfile(
    "Лак и Точка",
    (Service("Маникюр с покрытием", 1500), Service("Педикюр")),
    "ул. Ленина, 5",
    ("Стерильные инструменты", "Скидка 10% на первый визит", "Работаем на гель-лаке OPI"),
)


def kinds(draft: AdDraft, profile: BusinessProfile = PROFILE) -> set[ViolationKind]:
    return {violation.kind for violation in check_draft(draft, profile, RULES)}


def test_facts_from_profile_pass() -> None:
    assert not kinds(AdDraft("Маникюр с покрытием за 1500 ₽", "Стерильные инструменты. Ленина, 5."))


def test_price_with_space_matches_profile() -> None:
    assert not kinds(AdDraft("Маникюр за 1 500 ₽", "Запишитесь онлайн."))


@pytest.mark.parametrize(
    "draft",
    [
        AdDraft("Маникюр за 990 ₽", "Запишитесь онлайн."),
        AdDraft("Маникюр", "Опыт мастеров 12 лет."),
        AdDraft("Маникюр", "Скидка 30% сегодня."),
    ],
)
def test_invented_number_is_rejected(draft: AdDraft) -> None:
    assert ViolationKind.UNCONFIRMED_NUMBER in kinds(draft)


@pytest.mark.parametrize("word", ["бесплатно", "подарок", "отзывы", "лицензия"])
def test_promise_absent_from_profile_is_rejected(word: str) -> None:
    assert ViolationKind.UNCONFIRMED_CLAIM in kinds(AdDraft("Маникюр", f"Для вас {word}."))


def test_confirmed_discount_passes() -> None:
    assert not kinds(AdDraft("Маникюр: скидка 10%", "На первый визит."))


def test_discount_word_without_profile_fact_is_rejected() -> None:
    other = BusinessProfile("Лак", (Service("Маникюр"),))
    assert ViolationKind.UNCONFIRMED_CLAIM in kinds(AdDraft("Маникюр", "Скидка для вас."), other)


@pytest.mark.parametrize(
    "text",
    ["Лучший салон района.", "Гарантируем результат.", "Вылечим ногти.", "Самая низкая цена"],
)
def test_forbidden_words(text: str) -> None:
    assert ViolationKind.FORBIDDEN_WORD in kinds(AdDraft("Маникюр", text))


def test_word_inside_longer_word_is_not_forbidden() -> None:
    assert ViolationKind.FORBIDDEN_WORD not in kinds(AdDraft("Маникюр", "Самостоятельно не нужно."))


def test_foreign_word_is_rejected_unless_from_profile() -> None:
    assert ViolationKind.FOREIGN_WORD in kinds(AdDraft("Маникюр", "Настоящий premium сервис."))
    assert ViolationKind.FOREIGN_WORD not in kinds(AdDraft("Маникюр", "Гель-лак OPI."))


def test_length_limits_on_the_boundary() -> None:
    assert ViolationKind.TOO_LONG not in kinds(AdDraft("а" * 56, "б" * 81))
    assert ViolationKind.TOO_LONG in kinds(AdDraft("а" * 57, "б"))
    assert ViolationKind.TOO_LONG in kinds(AdDraft("а", "б" * 82))


def test_narrow_characters_have_their_own_quota() -> None:
    assert ViolationKind.TOO_LONG not in kinds(AdDraft("а", "б" * 81 + "." * 15))
    assert ViolationKind.TOO_LONG in kinds(AdDraft("а", "б" * 81 + "." * 16))


def test_empty_draft_is_rejected() -> None:
    assert ViolationKind.EMPTY in kinds(AdDraft(" ", "Текст"))


def test_similar_drafts_are_detected() -> None:
    first = AdDraft("Маникюр рядом с домом", "Запишитесь онлайн сегодня.")
    assert similarity_bp(first, first) == 10000
    assert too_similar_to_any(
        AdDraft("Маникюр рядом с домом", "Запишитесь онлайн."), [first], RULES
    )
    assert not too_similar_to_any(AdDraft("Педикюр", "Стерильные инструменты."), [first], RULES)


def test_templates_pass_their_own_checks_and_use_only_profile() -> None:
    drafts = template_drafts(PROFILE, RULES)
    assert drafts
    assert drafts[0].headline == "Маникюр с покрытием за 1500 ₽"
    for draft in drafts:
        assert not check_draft(draft, PROFILE, RULES)


text_part = st.text(alphabet="абвгдеёжз ", min_size=1, max_size=20).map(str.strip).filter(bool)


@given(name=text_part, service=text_part, price=st.none() | st.integers(1, 100000))
def test_templates_never_fail_checks_for_any_profile(
    name: str, service: str, price: int | None
) -> None:
    profile = BusinessProfile(name, (Service(service, price),))
    for draft in template_drafts(profile, RULES):
        assert not check_draft(draft, profile, RULES)


STEMS = load_rules("creatives-2")


def test_templates_without_business_name_have_no_empty_quotes() -> None:
    # Профиль, заполненный вручную, имени может не содержать: «в «»» в рекламу не идёт.
    profile = BusinessProfile(
        name="", services=(Service("Тестовая стрижка", 1000),), address="Екатеринбург", features=()
    )

    drafts = template_drafts(profile, RULES)

    assert drafts
    assert all("«»" not in f"{d.headline} {d.text}" for d in drafts)


def stem_kinds(draft: AdDraft, profile: BusinessProfile = PROFILE) -> set[ViolationKind]:
    return {violation.kind for violation in check_draft(draft, profile, STEMS)}


def test_stems_catch_other_word_forms() -> None:
    # creatives-1 знает только «скидка», «скидку», «скидки»: «скидкой» проходило.
    plain = BusinessProfile("Лак", (Service("Педикюр", 2000),))
    draft = AdDraft("Педикюр со скидкой", "Запишитесь онлайн.")
    assert ViolationKind.UNCONFIRMED_CLAIM not in kinds(draft, plain)
    assert ViolationKind.UNCONFIRMED_CLAIM in stem_kinds(draft, plain)


def test_stems_accept_other_form_from_profile() -> None:
    # В профиле «бесплатно», в объявлении «бесплатная»: creatives-1 снимал зря.
    cafe = BusinessProfile(
        "Пышка", (Service("Бизнес-ланч", 390),), features=("Доставка бесплатно",)
    )
    draft = AdDraft("Бесплатная доставка", "Кафе «Пышка».")
    assert ViolationKind.UNCONFIRMED_CLAIM in kinds(draft, cafe)
    assert ViolationKind.UNCONFIRMED_CLAIM not in stem_kinds(draft, cafe)
