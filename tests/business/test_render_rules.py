"""Когда статического текста мало и сайт читает браузер: правила site-2."""

import pytest

from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.domain.site import has_price, needs_render

RULES = load_site_rules("site-2")
RICH = "Студия маникюра. " * 100 + "Маникюр с покрытием — 1 500 ₽"


def test_site_2_reads_with_browser_and_site_1_does_not() -> None:
    assert RULES.render is not None
    assert RULES.render.below_chars == 1500
    assert {"tilda", "taplink", "yclients", "marquiz"} <= set(RULES.render.markers)
    # Старые разборы повторяются по своим правилам: у site-1 браузера нет.
    assert load_site_rules("site-1").render is None


@pytest.mark.parametrize(
    "text",
    [
        "Маникюр — 1 500 ₽",
        "Педикюр от 2000 руб.",
        "Стрижка 900 р.",
        "Окрашивание 3 000 рублей",
        "Брови 700р",
        "Массаж 1 200 ₽",
    ],
)
def test_prices_in_rubles_are_found(text: str) -> None:
    assert has_price(text)


@pytest.mark.parametrize(
    "text", ["Работаем с 2015 года", "Звоните +7 900 123-45-67", "Рядом с метро", ""]
)
def test_numbers_without_rubles_are_not_prices(text: str) -> None:
    assert not has_price(text)


def test_rich_static_page_with_prices_is_kept() -> None:
    assert RULES.render is not None
    assert not needs_render(RICH, (), RULES.render)


def test_short_page_or_page_without_prices_goes_to_browser() -> None:
    assert RULES.render is not None
    assert needs_render("Nails at Taplink", (), RULES.render)
    assert needs_render(RICH.replace("1 500 ₽", "по записи"), (), RULES.render)


def test_site_builder_in_markup_goes_to_browser_even_with_rich_text() -> None:
    # Прайс Tilda и виджеты записи часто дорисовываются скриптом поверх готового текста.
    assert RULES.render is not None
    assert needs_render(RICH, ("tilda",), RULES.render)
