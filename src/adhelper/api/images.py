"""Свои фото владельца для объявлений: загрузка, список, просмотр, снятие из подбора.

Файл приходит телом запроса с типом картинки, без multipart: одно фото за запрос.
Код приводит его к требованиям Директа и убирает метаданные (docs/05-creatives.md §3).
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.api.cabinet import Owned, OwnedChecked
from adhelper.creatives.adapters.images_sql import SqlImages
from adhelper.creatives.adapters.photos import prepare_image
from adhelper.creatives.adapters.rules_file import load_image_rules
from adhelper.creatives.application.images import add_owner_photo, owner_photos

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


def _problem(problem: str, numbers: dict[str, int]) -> str:
    return PROBLEMS.get(problem, PROBLEMS["unreadable"]).format(**numbers)


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    return sessions


def images_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}/photos")

    @router.get("")
    async def get_photos(request: Request, business_id: Owned) -> dict[str, Any]:
        found = await owner_photos(SqlImages(_sessions(request), business_id))
        base = f"/api/businesses/{business_id}/photos"
        return {
            "photos": [
                {
                    "id": image.id,
                    "width": image.width,
                    "height": image.height,
                    "url": f"{base}/{image.id}",
                }
                for image in found
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
