"""Приведение картинки к требованиям Директа: кадр, размер, JPEG без метаданных."""

import hashlib
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from adhelper.creatives.domain.images import (
    JPEG,
    ImageFacts,
    ImageProblem,
    ImageRejected,
    ImageRules,
    PreparedImage,
    check_image,
    crop_box,
    fit_size,
)

READABLE_FORMATS = frozenset({"JPEG", "PNG", "GIF", "WEBP"})


def prepare_image(raw: bytes, rules: ImageRules) -> PreparedImage:
    """Любая картинка становится JPEG допустимой пропорции.

    Метаданные не переносятся: в EXIF фото с телефона бывают координаты съёмки.
    """
    if len(raw) > rules.max_upload_bytes:
        raise ImageRejected((ImageProblem.TOO_HEAVY,))
    try:
        opened = Image.open(io.BytesIO(raw))
        if opened.format not in READABLE_FORMATS:
            raise ImageRejected((ImageProblem.FORMAT,))
        width, height = opened.size
        # Размер известен до декодирования: огромный кадр не распаковываем.
        if width * height > rules.max_decode_pixels:
            raise ImageRejected((ImageProblem.TOO_LARGE,))
        opened.load()
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ImageRejected((ImageProblem.UNREADABLE,)) from None
    image: Image.Image = ImageOps.exif_transpose(opened)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    image = image.crop(crop_box(*image.size))
    size = fit_size(*image.size, rules.store_max_side)
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=rules.jpeg_quality, optimize=True)
    data = output.getvalue()
    facts = ImageFacts(image.width, image.height, len(data), JPEG)
    problems = check_image(facts, rules)
    if problems:
        raise ImageRejected(problems)
    return PreparedImage(data, hashlib.sha256(data).hexdigest(), facts)
