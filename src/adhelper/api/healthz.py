"""Проверка жизнеспособности сервиса (/healthz).

Проверяет доступность базы данных и актуальность меток сердцебиения воркеров (worker_heartbeats).
Если база не отвечает или фоновый воркер завис (отметка старше 5 минут), отдаётся 503,
чтобы супервизор или платформа (Render) штатно перезапустили контейнер.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

router = APIRouter()

# Порог отставания сердцебиения: 5 минут без обновлений считается зависанием воркера.
STALE_HEARTBEAT_THRESHOLD_SECONDS = 300


async def check_health(engine: AsyncEngine) -> tuple[int, dict[str, Any]]:
    """Проверка доступности БД и актуальности сердцебиения фоновых воркеров."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

            exists = await conn.scalar(text("SELECT to_regclass('worker_heartbeats')"))
            if exists:
                stale_query = text(
                    "SELECT worker_name, "
                    "EXTRACT(EPOCH FROM (now() - last_heartbeat))::int AS lag_seconds "
                    "FROM worker_heartbeats "
                    "WHERE last_heartbeat < now() - interval '5 minutes'"
                )
                rows = (await conn.execute(stale_query)).fetchall()
                if rows:
                    details = ", ".join(f"{row[0]} (lag: {row[1]}s)" for row in rows)
                    logger.error("Сердцебиение воркеров устарело: %s", details)
                    return status.HTTP_503_SERVICE_UNAVAILABLE, {
                        "status": "unavailable",
                        "detail": f"stale worker heartbeat: {details}",
                    }
    except (SQLAlchemyError, OSError) as error:
        # В логе только тип ошибки: строка может содержать учётные данные БД
        logger.error("Проверка базы не прошла: %s", type(error).__name__)
        return status.HTTP_503_SERVICE_UNAVAILABLE, {"status": "unavailable"}

    return status.HTTP_200_OK, {"status": "ok"}


# Внешние мониторы ходят и на /api/healthz: без него шумят в логах 404 и 405.
@router.api_route("/healthz", methods=["GET", "HEAD", "POST"])
@router.api_route("/api/healthz", methods=["GET", "HEAD", "POST"], include_in_schema=False)
async def healthz(request: Request) -> JSONResponse:
    code, payload = await check_health(request.app.state.engine)
    return JSONResponse(payload, status_code=code)
