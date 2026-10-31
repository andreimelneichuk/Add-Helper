import io

from font_roboto import RobotoBold  # type: ignore[import-untyped]
from PIL import Image, ImageDraw, ImageFont

from adhelper.creatives.domain.cards import CARD_SIZES, CardFormat, CardPalette, contrast_x100
from adhelper.creatives.domain.model import AdDraft

MARGIN_SHARE = 12
HEADLINE_MAX_PT = 150
TEXT_RATIO_PERCENT = 45
MIN_PT = 28
FontType = ImageFont.FreeTypeFont


class CardDoesNotFit(Exception):
    """Текст не помещается: обрезать рекламу нельзя, карточка не создаётся."""


class LowContrast(Exception):
    pass


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: FontType, width: int) -> list[str] | None:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            current = trial
            continue
        if draw.textlength(word, font=font) > width:
            return None
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return [line for line in lines if line]


def _layout(
    draw: ImageDraw.ImageDraw, draft: AdDraft, width: int, height: int
) -> tuple[list[str], FontType, list[str], FontType, int, int]:
    for size in range(HEADLINE_MAX_PT, MIN_PT - 1, -2):
        big = ImageFont.truetype(RobotoBold, size)
        small = ImageFont.truetype(RobotoBold, max(MIN_PT, size * TEXT_RATIO_PERCENT // 100))
        big_step = int(size * 1.2)
        small_step = int(small.size * 1.3)
        headline = _wrap(draw, draft.headline, big, width)
        text = _wrap(draw, draft.text, small, width)
        if headline is None or text is None:
            continue
        total = len(headline) * big_step + big_step // 2 + len(text) * small_step
        if total <= height:
            return headline, big, text, small, big_step, small_step
    raise CardDoesNotFit(draft.headline)


def render_card(
    draft: AdDraft, palette: CardPalette, card_format: CardFormat, min_contrast_x100: int
) -> bytes:
    """Текст всегда накладывает наш код: модель буквы не рисует."""
    if contrast_x100(palette.text, palette.background) < min_contrast_x100:
        raise LowContrast(palette.name)
    width, height = CARD_SIZES[card_format]
    margin = min(width, height) // MARGIN_SHARE
    image = Image.new("RGB", (width, height), palette.background)
    draw = ImageDraw.Draw(image)
    bar = margin // 4
    inner_width = width - 2 * margin
    inner_height = height - 2 * margin - 2 * bar
    headline, big, text, small, big_step, small_step = _layout(
        draw, draft, inner_width, inner_height
    )
    draw.rectangle(
        (margin, margin, margin + inner_width // 4, margin + bar // 2), fill=palette.accent
    )
    y = margin + 2 * bar
    for line in headline:
        draw.text((margin, y), line, font=big, fill=palette.text)
        y += big_step
    y += big_step // 2
    for line in text:
        draw.text((margin, y), line, font=small, fill=palette.text)
        y += small_step
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
