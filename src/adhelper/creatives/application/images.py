"""Картинки объявлений: свои фото владельца и сцены генератора (docs/05-creatives.md §3).

Своё фото — приоритет: настоящая работа отвечает на главный вопрос клиента. Сцену генерируем,
только когда своих фото нет, одну на направление, в пределах суточного лимита. Сбой генератора
не держит объявление: оно уходит без картинки, деньги и показы от этого не зависят.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from adhelper.creatives.domain.images import (
    PROMPT_SCHEMA,
    PROMPT_SYSTEM,
    ImageModel,
    ImageProblem,
    ImageRejected,
    ImageRules,
    ImageSource,
    ImageStore,
    ImageUnavailable,
    NewImage,
    PreparedImage,
    ScenePrompt,
    StoredImage,
    choose_scene,
    pick_owner_photo,
    prompt_request,
)
from adhelper.creatives.domain.model import ModelUnavailable, TextModel
from adhelper.creatives.domain.ports import Outgoing

logger = logging.getLogger(__name__)

DAY = timedelta(hours=24)
Prepare = Callable[[bytes], PreparedImage]


@dataclass(frozen=True)
class Added:
    image_id: int | None
    problems: tuple[ImageProblem, ...] = ()


async def add_owner_photo(
    store: ImageStore,
    raw: bytes,
    consent: bool,
    prepare: Prepare,
    rules: ImageRules,
    now: datetime,
) -> Added:
    """Своё фото владельца. Без согласия на людей на фото не сохраняем (docs/05 §5)."""
    if not consent:
        raise ValueError("consent required")
    if len(await store.active(ImageSource.OWNER)) >= rules.max_owner_photos:
        return Added(None, (ImageProblem.TOO_MANY,))
    try:
        prepared = prepare(raw)
    except ImageRejected as rejected:
        return Added(None, rejected.problems)
    image = NewImage(prepared, ImageSource.OWNER, rules.version, consent=True)
    return Added(await store.add(image, now))


def previewable(raw: bytes, prepare: Prepare) -> bytes | None:
    """Готовый JPEG для показа владельцу или None, если фото не подходит."""
    try:
        return prepare(raw).data
    except ImageRejected:
        return None


async def owner_photos(store: ImageStore) -> Sequence[StoredImage]:
    """Свои фото владельца в подборе, по порядку добавления."""
    return await store.active(ImageSource.OWNER)


async def write_scene(
    writer: TextModel | None, niche: str, topic: str, headline: str, rules: ImageRules
) -> ScenePrompt:
    """Фото-запрос пишет текстовая модель; без неё или при плохом ответе — сцена из правил."""
    reply: str | None = None
    if writer is not None:
        try:
            answer = await writer.complete_json(
                PROMPT_SYSTEM,
                prompt_request(niche, topic, headline, rules),
                PROMPT_SCHEMA,
                rules.prompt_max_tokens,
            )
            reply = answer.text
        except ModelUnavailable:
            reply = None
    return choose_scene(reply, niche, topic, rules)


async def generate_scene(
    generators: Sequence[ImageModel],
    scene: ScenePrompt,
    prepare: Prepare,
    rules: ImageRules,
) -> tuple[PreparedImage, str, int | None] | None:
    """Основной генератор, при сбое запасной. Возвращает картинку, модель и цену."""
    for generator in generators:
        try:
            made = await generator.generate(scene.text, rules.aspect_ratio)
            return prepare(made.data), generator.name, made.cost_micro_usd
        except (ImageUnavailable, ImageRejected) as error:
            logger.warning("image generator %s failed: %s", generator.name, type(error).__name__)
    return None


async def attach_images(
    store: ImageStore,
    outgoing: Sequence[Outgoing],
    niche: str,
    writer: TextModel | None,
    generators: Sequence[ImageModel],
    prepare: Prepare,
    rules: ImageRules,
    now: datetime,
) -> int:
    """Картинки объявлениям, которые ждут отправки. Возвращает число поставленных."""
    attached = 0
    owner = [image.id for image in await store.active(ImageSource.OWNER)]
    used = list(await store.used())
    for item in outgoing:
        if item.image is not None or item.image_disabled:
            continue
        chosen = pick_owner_photo(owner, used)
        if chosen is None:
            chosen = await store.direction_image(item.account, item.campaign_id)
        if chosen is None and generators:
            if await store.generated_since(now - DAY) >= rules.generated_per_day:
                continue
            scene = await write_scene(writer, niche, "", item.draft.headline, rules)
            made = await generate_scene(generators, scene, prepare, rules)
            if made is None:
                # Генератор не ответил: в этом цикле больше не пробуем, объявление ждать не будет.
                generators = ()
                continue
            prepared, model, cost = made
            chosen = await store.add(
                NewImage(
                    prepared,
                    ImageSource.GENERATED,
                    rules.version,
                    consent=True,
                    account=item.account,
                    campaign_id=item.campaign_id,
                    prompt=scene.text,
                    prompt_source=scene.source,
                    model=model,
                    cost_micro_usd=cost,
                ),
                now,
            )
        if chosen is not None and await store.attach(item.creative_id, chosen):
            used.append(chosen)
            attached += 1
    return attached
