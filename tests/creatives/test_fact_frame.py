"""Рамка писателя в правилах creatives-3 (docs/12-agents.md §4).

Фрагмент-факт состоит только из слов своего факта и служебных слов; факт с числом пишется
целиком; вне фрагментов-фактов нет чисел и слов-свойств бизнеса.
"""

from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.domain.facts import Claim, check_claims, keyed_facts
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, Service

RULES = load_rules("creatives-3")
PROFILE = BusinessProfile(
    "Лак",
    (Service("Маникюр", 1500), Service("Уборка", 180)),
    "ул. Ленина, 5",
    ("Работаем пн–сб с 9 до 20", "Скидка 10% по будням до 12", "Стерильные инструменты"),
)
FACTS = keyed_facts(PROFILE)


def found(headline: str, text: str, *claims: tuple[str, str]) -> list[str]:
    draft = AdDraft(headline, text)
    return [v.detail for v in check_claims(draft, [Claim(*c) for c in claims], FACTS, RULES)]


def test_fact_copied_with_other_endings_passes() -> None:
    assert (
        found(
            "Маникюр за 1500 рублей",
            "Работаем пн–сб с 9 до 20. Стерильными инструментами. Ждём вас!",
            ("Маникюр за 1500 рублей", "s1_price"),
            ("Работаем пн–сб с 9 до 20", "f1"),
            ("Стерильными инструментами", "f3"),
        )
        == []
    )


def test_address_abbreviation_may_be_expanded() -> None:
    assert found("Маникюр", "Улица Ленина, 5.", ("Улица Ленина, 5", "address")) == []


def test_new_word_in_fact_fragment_is_violation() -> None:
    # «ежедневно» нет в факте о графике.
    assert found(
        "Маникюр", "Работаем ежедневно с 9 до 20.", ("Работаем ежедневно с 9 до 20", "f1")
    ) == ["слово «ежедневно» не из факта f1", "в факте f1 не хватает «пн», «сб»"]


def test_from_before_fixed_price_is_violation() -> None:
    assert found("Уборка от 180 ₽", "Ждём вас.", ("Уборка от 180 ₽", "s2_price")) == [
        "слово «от» не из факта s2_price"
    ]


def test_numeric_fact_must_keep_its_condition() -> None:
    assert found("Скидка 10% по будням", "Ждём вас.", ("Скидка 10% по будням", "f2")) == [
        "в факте f2 не хватает «до», «12»"
    ]


def test_fact_without_number_may_be_shortened() -> None:
    assert found("Маникюр", "Стерильные инструменты.", ("Стерильные", "f3")) == []


def test_property_outside_facts_is_violation() -> None:
    assert found("Маникюр", "Приезжаю к вам домой.", ("Маникюр", "s1")) == ["«приезжаю» без факта"]


def test_schedule_word_outside_facts_is_violation() -> None:
    assert found("Маникюр без выходных", "Ждём вас.", ("Маникюр", "s1")) == [
        "«без» без факта",
        "«выходных» без факта",
    ]


def test_call_to_action_and_client_thought_pass() -> None:
    assert found("Ногти снова в порядке?", "Запишитесь на маникюр.", ("маникюр", "s1")) == []


def test_negation_is_significant() -> None:
    assert found("Маникюр", "Не стерильные инструменты.", ("Не стерильные инструменты", "f3")) == [
        "слово «не» не из факта f3"
    ]


def test_old_rules_keep_old_behaviour() -> None:
    old = load_rules("creatives-2")
    draft = AdDraft("Маникюр от 1500 ₽", "Приезжаю домой.")
    assert check_claims(draft, [Claim("1500 ₽", "s1_price")], FACTS, old) == ()
