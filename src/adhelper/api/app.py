import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from adhelper.accounts.adapters.crypto import AesGcmCipher
from adhelper.accounts.adapters.sql import SqlAccountTokens, SqlIdentityStore, SqlUserScope
from adhelper.accounts.adapters.unconfigured import UnconfiguredProvider
from adhelper.accounts.application.service import AccountsService, OAuthProvider
from adhelper.api.accounts import accounts_router
from adhelper.api.cabinet import cabinet_router
from adhelper.api.control import control_router
from adhelper.api.display import display_router
from adhelper.api.notifications import notifications_router
from adhelper.config import Settings
from adhelper.db import create_engine, ping

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _no_platform(_business_id: UUID, _account: str) -> None:
    return None


def _accounts_service(
    settings: Settings, engine: AsyncEngine, provider: OAuthProvider | None
) -> AccountsService | None:
    keys = settings.token_encryption_keys
    if keys is None or not keys.get_secret_value():
        return None
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return AccountsService(
        SqlIdentityStore(sessions),
        lambda user_id: SqlUserScope(sessions, user_id),
        lambda business_id: SqlAccountTokens(sessions, business_id),
        # Без провайдера сессии работают, а новый вход и подключение кабинета отвечают 503.
        provider or UnconfiguredProvider(),
        AesGcmCipher.from_config(keys.get_secret_value()),
        _utc_now,
        settings.public_base_url,
    )


def create_app(
    settings: Settings | None = None, oauth_provider: OAuthProvider | None = None
) -> FastAPI:
    settings = settings or Settings()
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="Ad Helper", lifespan=lifespan)
    app.state.engine = engine
    # Провайдер Яндекса появится после реального вызова (V1); до этого вход отвечает 503.
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app.state.sessions = sessions
    app.state.clock = _utc_now
    app.state.accounts = _accounts_service(settings, engine, oauth_provider)
    app.include_router(accounts_router(settings))
    app.include_router(cabinet_router())
    app.include_router(control_router())
    app.include_router(display_router())
    # Адаптер Директа появится в T5 (ждёт V1); пока действия с площадкой честно отвечают 503.
    app.state.platform_for = _no_platform
    app.include_router(notifications_router())
    token = settings.telegram_bot_token
    # Без токена бот не читает сообщения, и ссылка на привязку никуда бы не вела.
    has_bot = token is not None and bool(token.get_secret_value())
    app.state.telegram_bot = settings.telegram_bot_name if has_bot else ""

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
