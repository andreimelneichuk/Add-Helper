"""Ссылки писателя на факты профиля проверяет код (docs/12-agents.md §4)."""

from adhelper.creatives.domain.facts import Claim, check_claims, keyed_facts
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, Service, ViolationKind

PROFILE = BusinessProfile(
    "Лак",
    (Service("Маникюр", 1500), Service("Педикюр", 2000), Service("Брови")),
    "ул. Ленина, 5",
    ("Стерильные инструменты",),
)
FACTS = keyed_facts(PROFILE)


def test_facts_have_short_stable_keys() -> None:
    assert FACTS == {
        "name": "Лак",
        "address": "ул. Ленина, 5",
        "s1": "Маникюр",
        "s1_price": "Маникюр: 1500 ₽",
        "s2": "Педикюр",
        "s2_price": "Педикюр: 2000 ₽",
        "s3": "Брови",
        "f1": "Стерильные инструменты",
    }


def test_bound_claims_pass() -> None:
    draft = AdDraft("Маникюр за 1500 ₽", "Стерильные инструменты. Ждём: ул. Ленина, 5.")
    claims = (
        Claim("Маникюр за 1500 ₽", "s1_price"),
        Claim("стерильные инструменты", "f1"),
        Claim("ул. Ленина, 5", "address"),
    )
    assert check_claims(draft, claims, FACTS) == ()


def test_unknown_key_is_violation() -> None:
    draft = AdDraft("Маникюр", "Ждём вас.")
    found = check_claims(draft, (Claim("Маникюр", "s9"),), FACTS)
    assert [v.kind for v in found] == [ViolationKind.UNBOUND_CLAIM]


def test_fragment_must_be_in_ad() -> None:
    draft = AdDraft("Маникюр", "Ждём вас.")
    found = check_claims(draft, (Claim("Педикюр", "s2"),), FACTS)
    assert [v.kind for v in found] == [ViolationKind.UNBOUND_CLAIM]


def test_number_without_claim_is_violation() -> None:
    # Число есть в профиле, но писатель не сказал, откуда оно.
    found = check_claims(AdDraft("Маникюр за 1500 ₽", "Ждём вас."), (), FACTS)
    assert [v.detail for v in found] == ["число 1500 без факта"]


def test_price_of_other_service_is_violation() -> None:
    # Цена педикюра в объявлении о маникюре: глобальная проверка чисел её не заметит.
    draft = AdDraft("Маникюр за 2000 ₽", "Ждём вас.")
    found = check_claims(draft, (Claim("Маникюр за 2000 ₽", "s1_price"),), FACTS)
    assert [v.detail for v in found] == ["число 2000 без факта"]


def test_address_number_bound_to_address() -> None:
    draft = AdDraft("Маникюр", "Ждём: ул. Ленина, 5.")
    assert check_claims(draft, (Claim("ул. Ленина, 5", "address"),), FACTS) == ()
