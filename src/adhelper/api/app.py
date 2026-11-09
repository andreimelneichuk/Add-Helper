import hashlib
import hmac
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from adhelper.accounts.adapters.crypto import AesGcmCipher
from adhelper.accounts.adapters.sql import SqlAccountTokens, SqlIdentityStore, SqlUserScope
from adhelper.accounts.adapters.unconfigured import UnconfiguredProvider
from adhelper.accounts.application.service import AccountsService, OAuthProvider, TokenInvalid
from adhelper.api.accounts import accounts_router
from adhelper.api.autopilot import autopilot_router
from adhelper.api.cabinet import cabinet_router
from adhelper.api.chat import chat_router
from adhelper.api.control import control_router
from adhelper.api.display import display_router
from adhelper.api.economics import economics_router, public_economics_router
from adhelper.api.experiments import experiments_router
from adhelper.api.healthz import router as healthz_router
from adhelper.api.images import images_router
from adhelper.api.launch import launch_router
from adhelper.api.notifications import notifications_router
from adhelper.api.plan import plan_router
from adhelper.api.rework import rework_router
from adhelper.api.site import guest_site_router, site_router
from adhelper.api.wizard_ads import guest_ads_router, wizard_ads_router
from adhelper.commands.application.service import CallBudget
from adhelper.config import Settings
from adhelper.creatives.adapters.factory import build_text_model
from adhelper.db import create_engine
from adhelper.platforms.adapters.direct.factory import (
    DirectPlatforms,
    build_direct_platforms,
    yandex_provider,
)
from adhelper.platforms.adapters.direct.transport import DirectAuthError

logger = logging.getLogger(__name__)

# Те же заголовки, что ставит deploy/Caddyfile: приложение защищено и без Caddy.
_SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


# Как в deploy/Caddyfile: запросы сервиса — короткий JSON. Фото владельца читается потоком
# со своим пределом из правил картинок, поэтому ему лимит не ставим.
_MAX_BODY = 1_000_000


def _body_refused(request: Request) -> Response | None:
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    if request.url.path.endswith("/photos"):
        return None
    length = request.headers.get("content-length")
    if length is None:
        if "chunked" in request.headers.get("transfer-encoding", "").lower():
            return JSONResponse({"detail": "Нужна длина запроса"}, status_code=411)
        return None
    if not length.isdigit() or int(length) > _MAX_BODY:
        return JSONResponse({"detail": "Запрос слишком большой"}, status_code=413)
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _no_platform(_business_id: UUID, _account: str) -> None:
    return None


def _cipher(settings: Settings) -> AesGcmCipher | None:
    keys = settings.token_encryption_keys
    if keys is None or not keys.get_secret_value():
        return None
    return AesGcmCipher.from_config(keys.get_secret_value())


def _accounts_service(
    engine: AsyncEngine,
    provider: OAuthProvider | None,
    cipher: AesGcmCipher | None,
    base_url: str,
    resolve_login: Callable[[str], Awaitable[str | None]],
) -> AccountsService | None:
    if cipher is None:
        return None
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return AccountsService(
        SqlIdentityStore(sessions),
        lambda user_id: SqlUserScope(sessions, user_id),
        lambda business_id: SqlAccountTokens(sessions, business_id, cipher),
        # Без провайдера сессии работают, а новый вход и подключение кабинета отвечают 503.
        provider or UnconfiguredProvider(),
        cipher,
        _utc_now,
        base_url,
        resolve_login,
    )


def _guest_secret(settings: Settings) -> bytes:
    """Ключ HMAC для лимита гостей. Без ключей шифрования он свой у процесса, и лимит
    обнуляется при перезапуске: это хуже, но адрес человека всё равно не хранится."""
    keys = settings.token_encryption_keys
    if keys is None or not keys.get_secret_value():
        return secrets.token_bytes(32)
    material = keys.get_secret_value().encode()
    return hmac.new(material, b"site-guest-limit", hashlib.sha256).digest()


def create_app(
    settings: Settings | None = None, oauth_provider: OAuthProvider | None = None
) -> FastAPI:
    settings = settings or Settings()
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        platforms = getattr(app.state, "platform_for", None)
        if isinstance(platforms, DirectPlatforms):
            await platforms.aclose()
        await engine.dispose()

    # Служебные страницы наружу не отдаём: сервис может стоять без Caddy (например, на Render).
    app = FastAPI(
        title="Ad Helper",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        refused = _body_refused(request)
        response = refused if refused is not None else await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.state.engine = engine
    # Без приложения Яндекс ID в настройках вход честно отвечает 503.
    oauth_provider = oauth_provider or yandex_provider(settings, _utc_now)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app.state.sessions = sessions
    app.state.clock = _utc_now
    # Без ключей шифрования нет ни входа, ни хранения токенов и ИНН.
    app.state.cipher = _cipher(settings)

    async def resolve_login(token: str) -> str | None:
        # Площадки появляются ниже: берём их в момент вызова.
        platforms = getattr(app.state, "platform_for", None)
        if isinstance(platforms, DirectPlatforms):
            try:
                return await platforms.owner_login(token)
            except DirectAuthError as error:
                raise TokenInvalid("Токен отклонён Яндекс Директом") from error
        return None

    app.state.accounts = _accounts_service(
        engine, oauth_provider, app.state.cipher, settings.public_base_url, resolve_login
    )
    app.include_router(accounts_router(settings))
    app.include_router(cabinet_router())
    app.include_router(control_router())
    app.include_router(display_router())
    app.include_router(site_router())
    app.include_router(guest_site_router())
    app.include_router(plan_router())
    app.include_router(launch_router())
    app.include_router(rework_router())
    app.include_router(guest_ads_router())
    app.include_router(wizard_ads_router())
    app.include_router(experiments_router())
    app.include_router(autopilot_router())
    app.include_router(economics_router())
    app.include_router(public_economics_router())
    app.state.guest_secret = _guest_secret(settings)
    # Без DIRECT_ENABLED действия с площадкой честно отвечают 503.
    platforms = build_direct_platforms(
        settings, sessions, oauth_provider, app.state.cipher, _utc_now
    )
    app.state.platform_for = platforms or _no_platform
    # Режим «Сам» у автопилота до ворот этапа 9 закрыт (docs/17-autopilot-plan.md).
    app.state.autopilot_auto_allowed = settings.autopilot_auto_allowed
    app.include_router(notifications_router())
    app.include_router(chat_router())
    app.include_router(images_router())
    # Разборщик команд и генерация: без ключа модели сервис работает на правилах.
    app.state.command_model = build_text_model(
        settings.text_model_base_url,
        settings.text_model_api_key,
        settings.text_model_name,
        settings.text_model_project,
    )
    app.state.text_model = app.state.command_model
    from adhelper.commands.application.langchain_agent import build_langchain_agent

    app.state.langchain_agent = build_langchain_agent(
        settings.text_model_base_url,
        settings.text_model_api_key,
        settings.text_model_name,
        settings.text_model_project,
    )
    app.state.command_calls = CallBudget()
    token = settings.telegram_bot_token
    # Без токена бот не читает сообщения, и ссылка на привязку никуда бы не вела.
    has_bot = token is not None and bool(token.get_secret_value())
    app.state.telegram_bot = settings.telegram_bot_name if has_bot else ""
    app.include_router(healthz_router)

    frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend_dist.is_dir():
        assets_dir = frontend_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> Response:
            # Служебные адреса не отдаём и как интерфейс: как в Caddyfile, это 404.
            if full_path in ("docs", "redoc", "openapi.json"):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
            if full_path.startswith(("api/", "stand-yandex/")):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
            root = frontend_dist.resolve()
            candidate = (root / full_path).resolve()
            if full_path and candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            # Страница ссылается на файлы с хешем в имени: без этого заголовка браузер держит старую
            # страницу и после выкладки показывает прежний интерфейс.
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app
