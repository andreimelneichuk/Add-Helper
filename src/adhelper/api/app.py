import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from adhelper.config import Settings
from adhelper.db import create_engine, ping

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    engine = create_engine(settings or Settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="Ad Helper", lifespan=lifespan)
    app.state.engine = engine

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        try:
            await ping(request.app.state.engine)
        except (SQLAlchemyError, OSError) as error:
            # В логе только тип ошибки: текст может содержать адрес подключения.
            logger.error("Проверка базы не прошла: %s", type(error).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return app
