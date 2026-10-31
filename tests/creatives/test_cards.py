"""Шаблоны карточек: форматы, контраст, текст не обрезается."""

import io

import pytest
from PIL import Image

from adhelper.creatives.adapters.cards import CardDoesNotFit, LowContrast, render_card
from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.domain.cards import (
    CARD_SIZES,
    PALETTES,
    CardFormat,
    CardPalette,
    contrast_x100,
)
from adhelper.creatives.domain.model import AdDraft

RULES = load_rules("creatives-1")
DRAFT = AdDraft("Маникюр с покрытием за 1500 ₽", "Стерильные инструменты. Ждём вас: ул. Ленина, 5.")
LONGEST = AdDraft("Щ" * 20 + " " + "Ж" * 20 + " " + "Ш" * 14, " ".join(["Шщ" * 4] * 9))


def image_of(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


@pytest.mark.parametrize("card_format", list(CardFormat))
@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_every_template_renders_at_required_size(
    card_format: CardFormat, palette: CardPalette
) -> None:
    image = image_of(render_card(DRAFT, palette, card_format, RULES.min_contrast_x100))
    assert image.size == CARD_SIZES[card_format]
    assert min(image.size) >= 1080


@pytest.mark.parametrize("palette", PALETTES, ids=lambda p: p.name)
def test_palettes_meet_contrast_rule(palette: CardPalette) -> None:
    assert contrast_x100(palette.text, palette.background) >= RULES.min_contrast_x100


def test_low_contrast_is_refused() -> None:
    murky = CardPalette("murky", (120, 120, 120), (135, 135, 135), (0, 0, 0))
    with pytest.raises(LowContrast):
        render_card(DRAFT, murky, CardFormat.SQUARE, RULES.min_contrast_x100)


def test_contrast_values() -> None:
    assert contrast_x100((0, 0, 0), (255, 255, 255)) == 2100
    assert contrast_x100((9, 9, 9), (9, 9, 9)) == 100


@pytest.mark.parametrize("card_format", list(CardFormat))
def test_longest_allowed_text_stays_inside_margins(card_format: CardFormat) -> None:
    palette = PALETTES[0]
    image = image_of(render_card(LONGEST, palette, card_format, RULES.min_contrast_x100))
    width, height = image.size
    margin = min(width, height) // 12
    pixels = image.load()
    assert pixels is not None
    for x in range(0, width, 3):
        for y in (*range(0, margin // 2, 5), *range(height - margin // 2, height, 5)):
            assert pixels[x, y] == palette.background
    for y in range(0, height, 3):
        for x in (*range(0, margin // 2, 5), *range(width - margin // 2, width, 5)):
            assert pixels[x, y] == palette.background


def test_text_too_long_is_refused_not_cut() -> None:
    huge = AdDraft("Маникюр", "слово " * 2000)
    with pytest.raises(CardDoesNotFit):
        render_card(huge, PALETTES[0], CardFormat.WIDE, RULES.min_contrast_x100)


def test_word_wider_than_card_is_refused() -> None:
    with pytest.raises(CardDoesNotFit):
        render_card(AdDraft("Щ" * 400, "текст"), PALETTES[0], CardFormat.SQUARE, 450)


def test_cyrillic_glyphs_are_really_drawn() -> None:
    first = render_card(AdDraft("Жук", "Щука"), PALETTES[0], CardFormat.SQUARE, 450)
    second = render_card(AdDraft("Лак", "Вода"), PALETTES[0], CardFormat.SQUARE, 450)
    assert first != second
    assert image_of(first).getcolors(maxcolors=100000) is not None
