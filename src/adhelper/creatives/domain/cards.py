from dataclasses import dataclass
from enum import StrEnum

Color = tuple[int, int, int]


class CardFormat(StrEnum):
    SQUARE = "1:1"
    WIDE = "16:9"
    PORTRAIT = "4:5"
    STORY = "9:16"


# Короткая сторона 1080 px: не меньше требования площадки в 450 px.
CARD_SIZES: dict[CardFormat, tuple[int, int]] = {
    CardFormat.SQUARE: (1080, 1080),
    CardFormat.WIDE: (1920, 1080),
    CardFormat.PORTRAIT: (1080, 1350),
    CardFormat.STORY: (1080, 1920),
}


@dataclass(frozen=True)
class CardPalette:
    name: str
    background: Color
    text: Color
    accent: Color


PALETTES = (
    CardPalette("ink", (24, 32, 44), (250, 247, 240), (242, 190, 92)),
    CardPalette("cream", (250, 244, 232), (40, 34, 30), (160, 64, 40)),
    CardPalette("sage", (222, 232, 218), (28, 48, 38), (32, 96, 72)),
    CardPalette("berry", (112, 28, 66), (255, 244, 240), (255, 205, 178)),
)


def _channel(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4


def _luminance(color: Color) -> float:
    red, green, blue = color
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_x100(first: Color, second: Color) -> int:
    """Контраст по WCAG, умноженный на 100, округлён вниз."""
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return int((high + 0.05) / (low + 0.05) * 100)
