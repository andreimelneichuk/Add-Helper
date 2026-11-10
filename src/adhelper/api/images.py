"""Свои фото владельца для объявлений: загрузка, список, просмотр, снятие из подбора.

Файл приходит телом запроса с типом картинки, без multipart: одно фото за запрос.
Код приводит его к требованиям Директа и убирает метаданные (docs/05-creatives.md §3).
"""

import base64
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.api.cabinet import Owned, OwnedChecked
from adhelper.business.adapters.browser import ChromiumRenderer
from adhelper.business.adapters.fetcher import SafeFetcher
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.adapters.site_images import download_many, site_image_urls
from adhelper.creatives.adapters.images_sql import SqlImages
from adhelper.creatives.adapters.photos import prepare_image
from adhelper.creatives.adapters.rules_file import load_image_rules
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.application.images import (
    GenerateProblem,
    add_owner_photo,
    generate_photo,
    generated_photos,
    match_photos,
    owner_photos,
    previewable,
)

ACCEPTED_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
# Тексты по причинам отказа: словами владельца, с тем, что сделать. Числа — из правил.
PROBLEMS = {
    "unreadable": "Не получилось открыть файл. Пришлите фото в JPG или PNG.",
    "format": "Такой формат Яндекс не примет. Пришлите фото в JPG, PNG или GIF.",
    "too_heavy": "Файл слишком тяжёлый. Пришлите фото до {mb} МБ.",
    "too_small": "Фото маленькое: нужно не меньше {side} точек по короткой стороне.",
    "too_large": "Фото слишком большое по числу точек. Уменьшите его и пришлите снова.",
    "ratio": "Фото слишком вытянутое. Подойдёт квадрат или кадр 4:3.",
    "too_many": "Уже {count} фото. Уберите одно, чтобы добавить новое.",
}
NO_CONSENT = (
    "Отметьте, что люди на фото согласны на рекламу или их на фото нет. Без этого фото не сохраню."
)
NOT_FOUND = "Фото не найдено"


class SiteUrl(BaseModel):
    url: str = Field(max_length=2000)


class GenerateBody(BaseModel):
    service: str = Field(min_length=1, max_length=200)


GENERATE_PROBLEMS = {
    GenerateProblem.DISABLED: "Генерация фото выключена. Загрузите своё фото.",
    GenerateProblem.LIMIT: (
        "Сегодня уже сделано {count} фото. Новое можно завтра, а пока выберите готовое "
        "или загрузите своё."
    ),
    GenerateProblem.UNAVAILABLE: "Не получилось сделать фото. Попробуйте позже или загрузите своё.",
}
NO_SERVICE = "Такой услуги нет в профиле. Фото рисуем только по услугам из профиля."


class DraftServices(BaseModel):
    services: list[str] = Field(default_factory=list, max_length=20)


class MatchBody(BaseModel):
    drafts: list[DraftServices] = Field(min_length=1, max_length=10)


class SiteImport(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=12)
    consent: bool = False


NO_SITE_IMAGES = "На странице не нашёл подходящих картинок. Добавьте фото вручную."
SITE_FAILED = "Не получилось открыть сайт. Проверьте адрес или добавьте фото вручную."


def _problem(problem: str, numbers: dict[str, int]) -> str:
    return PROBLEMS.get(problem, PROBLEMS["unreadable"]).format(**numbers)


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    return sessions


def images_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}/photos")

    @router.get("")
    async def get_photos(
        request: Request, business_id: Owned, include_generated: bool = Query(False)
    ) -> dict[str, Any]:
        store = SqlImages(_sessions(request), business_id)
        found = await owner_photos(store)
        # Сделанные сервисом фото владелец может выбрать для объявления, но в автоподбор
        # и в счёт «своих» 5 фото они не входят.
        extra = list(await generated_photos(store)) if include_generated else []
        base = f"/api/businesses/{business_id}/photos"
        return {
            "photos": [
                {
                    "id": image.id,
                    "width": image.width,
                    "height": image.height,
                    "url": f"{base}/{image.id}",
                    "source": image.source.value,
                }
                for image in [*found, *extra]
            ],
            "max": load_image_rules().max_owner_photos,
        }

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def post_photo(
        request: Request,
        business_id: OwnedChecked,
        consent: bool = Query(False),
    ) -> dict[str, Any]:
        if not consent:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_CONSENT)
        rules = load_image_rules()
        numbers = {
            "mb": rules.max_upload_bytes // 1_000_000,
            "side": rules.min_side,
            "count": rules.max_owner_photos,
        }
        kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if kind not in ACCEPTED_TYPES:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, _problem("format", numbers))
        # Тело читаем кусками и обрываем на пределе: большой файл не держим в памяти целиком.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > rules.max_upload_bytes:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE, _problem("too_heavy", numbers)
                )
        added = await add_owner_photo(
            SqlImages(_sessions(request), business_id),
            bytes(raw),
            consent,
            lambda data: prepare_image(data, rules),
            rules,
            request.app.state.clock(),
        )
        if added.image_id is None:
            text = " ".join(_problem(p.value, numbers) for p in added.problems)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, text)
        return {
            "id": added.image_id,
            "url": f"/api/businesses/{business_id}/photos/{added.image_id}",
        }

    @router.post("/match")
    async def post_match(
        request: Request, body: MatchBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        """Фото к каждому объявлению по смыслу. Деньги не тратит: модель только смотрит."""
        sessions = _sessions(request)
        profile = await SqlProfiles(sessions, business_id).get()
        known = {item.name.strip() for item in profile[0].services} if profile else set()
        # Список услуг для модели берём из профиля: чужие слова из запроса в него не попадают.
        drafts = [[name for name in item.services if name in known] for item in body.drafts]
        store = SqlImages(sessions, business_id)
        found = await match_photos(store, getattr(request.app.state, "photo_labeler", None), drafts)
        base = f"/api/businesses/{business_id}/photos"
        return {
            "matches": [
                {"image_id": m.image_id, "missing": m.missing, "fallback": m.fallback}
                for m in found
            ],
            "photos": [
                {
                    "id": image.id,
                    "width": image.width,
                    "height": image.height,
                    "url": f"{base}/{image.id}",
                    "source": image.source.value,
                }
                for image in await store.active()
            ],
        }

    @router.post("/generate", status_code=status.HTTP_201_CREATED)
    async def post_generate(
        request: Request, body: GenerateBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        profile = await SqlProfiles(sessions, business_id).get()
        service = body.service.strip()
        if profile is None or service not in {item.name.strip() for item in profile[0].services}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_SERVICE)
        rules = load_image_rules()
        made = await generate_photo(
            SqlImages(sessions, business_id),
            profile[0].niche,
            service,
            getattr(request.app.state, "text_model", None),
            getattr(request.app.state, "image_models", ()),
            lambda data: prepare_image(data, rules),
            rules,
            request.app.state.clock(),
        )
        if made.image_id is None:
            problem = made.problem or GenerateProblem.UNAVAILABLE
            code = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if problem is GenerateProblem.LIMIT
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            raise HTTPException(
                code, GENERATE_PROBLEMS[problem].format(count=rules.generated_per_day)
            )
        return {
            "id": made.image_id,
            "url": f"/api/businesses/{business_id}/photos/{made.image_id}",
            "source": "generated",
        }

    @router.post("/site-candidates")
    async def post_site_candidates(
        request: Request, business_id: OwnedChecked, body: SiteUrl
    ) -> dict[str, Any]:
        """Картинки сайта для выбора. Показываем уже приведёнными к требованиям, чтобы
        владелец видел, что именно попадёт в объявление; сохраняем только выбранные."""
        site_rules = load_site_rules()
        rules = load_image_rules()
        fetcher = SafeFetcher(site_rules)
        # Браузер поднимается, только если в разметке картинок мало.
        async with ChromiumRenderer(site_rules) as renderer:
            urls = await site_image_urls(fetcher, body.url, site_rules, renderer)
        if urls is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, SITE_FAILED)
        shown: list[dict[str, Any]] = []
        downloaded = await download_many(fetcher, urls, rules.max_upload_bytes)
        for url, raw in zip(urls, downloaded, strict=True):
            if raw is None:
                continue
            data = previewable(raw, lambda value: prepare_image(value, rules))
            if data is None:
                continue
            encoded = base64.b64encode(data).decode("ascii")
            shown.append({"url": url, "preview": f"data:image/jpeg;base64,{encoded}"})
            if len(shown) >= 8:
                break
        if not shown:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SITE_IMAGES)
        return {"images": shown}

    @router.post("/from-site", status_code=status.HTTP_201_CREATED)
    async def post_from_site(
        request: Request, business_id: OwnedChecked, body: SiteImport
    ) -> dict[str, Any]:
        if not body.consent:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_CONSENT)
        site_rules = load_site_rules()
        rules = load_image_rules()
        fetcher = SafeFetcher(site_rules)
        store = SqlImages(_sessions(request), business_id)
        added = 0
        skipped = 0
        downloaded = await download_many(fetcher, body.urls, rules.max_upload_bytes)
        for raw in downloaded:
            result = None
            if raw is not None:
                result = await add_owner_photo(
                    store,
                    raw,
                    True,
                    lambda data: prepare_image(data, rules),
                    rules,
                    request.app.state.clock(),
                )
            if result is not None and result.image_id is not None:
                added += 1
            else:
                skipped += 1
        return {"added": added, "skipped": skipped}

    @router.get("/{image_id}")
    async def get_photo(request: Request, image_id: int, business_id: Owned) -> Response:
        data = await SqlImages(_sessions(request), business_id).content(image_id)
        if data is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        # Картинка бизнеса: только в браузере владельца, общие кэши её не хранят.
        return Response(
            data,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/{image_id}/archive")
    async def post_archive(
        request: Request, image_id: int, business_id: OwnedChecked
    ) -> dict[str, Any]:
        archived = await SqlImages(_sessions(request), business_id).archive(
            image_id, request.app.state.clock()
        )
        if not archived:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        # Объявления, где фото уже стоит, работают дальше: новое в них не встанет.
        return {"archived": True}

    return router
