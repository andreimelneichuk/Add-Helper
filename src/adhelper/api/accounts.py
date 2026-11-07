import logging
import secrets
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from adhelper.accounts.application.service import (
    AccountsService,
    AccountTaken,
    BusinessNotFound,
    FlowInvalid,
    GrantRejected,
    LoginUnknown,
    ProviderUnavailable,
)
from adhelper.config import Settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ah_session"
FLOW_COOKIE = "ah_oauth_state"
FLOW_COOKIE_SECONDS = 600
# Куда вернуть после входа. Только из этого списка: чужой адрес из запроса не принимаем.
AFTER_LOGIN_COOKIE = "ah_after_login"
AFTER_LOGIN = {"start": "/?start"}
SESSION_COOKIE_SECONDS = 30 * 24 * 3600


class NewBusiness(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ManualToken(BaseModel):
    login: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,50}$")
    token: str = Field(min_length=20, max_length=500, pattern=r"^\S+$")


def _service(request: Request) -> AccountsService:
    service: AccountsService | None = request.app.state.accounts
    if service is None:
        # Нет провайдера или ключей: лучше отказать, чем хранить токены незашифрованными.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Вход пока не настроен")
    return service


Service = Annotated[AccountsService, Depends(_service)]


async def _session(request: Request, service: Service) -> Any:
    token = request.cookies.get(SESSION_COOKIE)
    info = await service.session(token) if token else None
    if info is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужно войти")
    return info


CurrentSession = Annotated[Any, Depends(_session)]


async def _csrf_checked(
    info: CurrentSession, x_csrf_token: Annotated[str | None, Header()] = None
) -> Any:
    if x_csrf_token is None or not secrets.compare_digest(x_csrf_token, info.csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Обновите страницу и повторите")
    return info


CheckedSession = Annotated[Any, Depends(_csrf_checked)]


def _flow_redirect(url: str, state: str, secure: bool) -> RedirectResponse:
    response = RedirectResponse(url, status.HTTP_302_FOUND)
    response.set_cookie(
        FLOW_COOKIE,
        state,
        max_age=FLOW_COOKIE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api",
    )
    return response


def _home(result: str = "", target: str = "/") -> RedirectResponse:
    url = target if not result else f"{target}{'&' if '?' in target else '?'}{result}"
    response = RedirectResponse(url, status.HTTP_302_FOUND)
    response.delete_cookie(FLOW_COOKIE, path="/api")
    response.delete_cookie(AFTER_LOGIN_COOKIE, path="/api")
    return response


def _state_matches_browser(request: Request, state: str | None) -> bool:
    """Ответ провайдера принимаем только в браузере, начавшем вход."""
    cookie = request.cookies.get(FLOW_COOKIE)
    return bool(state and cookie and secrets.compare_digest(state, cookie))


def _account_view(account: Any) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "platform": account.platform,
        "login": account.login,
        "scopes": list(account.scopes),
        "state": account.state.value,
    }


def accounts_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")
    # Сайт по https всегда отдаёт Secure-куки: COOKIE_SECURE=false нужен только локальному http.
    secure = settings.cookie_secure or settings.public_base_url.startswith("https://")

    @router.get("/auth/yandex/login")
    async def login(service: Service, then: str | None = None) -> RedirectResponse:
        try:
            url, state = await service.begin_login()
        except ProviderUnavailable:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Вход пока не настроен"
            ) from None
        response = _flow_redirect(url, state, secure)
        if then in AFTER_LOGIN:
            response.set_cookie(
                AFTER_LOGIN_COOKIE,
                then,
                max_age=FLOW_COOKIE_SECONDS,
                httponly=True,
                secure=secure,
                samesite="lax",
                path="/api",
            )
        return response

    @router.get("/auth/yandex/callback")
    async def login_callback(
        request: Request,
        service: Service,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        back = AFTER_LOGIN.get(request.cookies.get(AFTER_LOGIN_COOKIE, ""), "/")
        if not _state_matches_browser(request, state) or state is None:
            logger.warning("OAuth login state mismatch")
            return _home("login=failed", back)
        try:
            if error is not None or code is None:
                await service.cancel_login(state)
                logger.warning("OAuth login denied or error: code_is_none=%s", code is None)
                return _home("login=denied", back)
            session = await service.complete_login(state, code)
        except (FlowInvalid, GrantRejected) as exc:
            logger.warning("OAuth login failed on complete_login: %s", type(exc).__name__)
            return _home("login=failed", back)
        except ProviderUnavailable as exc:
            logger.warning("OAuth login provider unavailable: %s", type(exc).__name__)
            return _home("login=unavailable", back)
        response = _home(target=back)
        response.set_cookie(
            SESSION_COOKIE,
            session.token,
            max_age=SESSION_COOKIE_SECONDS,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return response

    @router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request, service: Service, _: CheckedSession) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            await service.logout(token)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/me")
    async def me(info: CurrentSession) -> dict[str, Any]:
        return {"email": info.user.email, "csrf_token": info.csrf_token}

    @router.post("/businesses", status_code=status.HTTP_201_CREATED)
    async def create_business(
        body: NewBusiness, service: Service, info: CheckedSession
    ) -> dict[str, str]:
        business = await service.create_business(info.user.id, body.name)
        return {"id": str(business.id), "name": business.name}

    @router.get("/businesses")
    async def businesses(service: Service, info: CurrentSession) -> list[dict[str, str]]:
        found = await service.businesses(info.user.id)
        return [{"id": str(item.id), "name": item.name} for item in found]

    @router.get("/businesses/{business_id}/accounts")
    async def accounts(
        business_id: UUID, service: Service, info: CurrentSession
    ) -> list[dict[str, Any]]:
        try:
            return [_account_view(a) for a in await service.accounts(info.user.id, business_id)]
        except BusinessNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Бизнес не найден") from None

    @router.get("/businesses/{business_id}/accounts/manual-token/link")
    async def manual_token_link(
        business_id: UUID, service: Service, info: CurrentSession
    ) -> dict[str, str]:
        if not settings.yandex_manual_client_id:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Выдача токена вручную не настроена"
            )
        try:
            await service.accounts(info.user.id, business_id)
        except BusinessNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Бизнес не найден") from None
        query = urlencode({"response_type": "token", "client_id": settings.yandex_manual_client_id})
        return {"url": f"{settings.yandex_oauth_url.rstrip('/')}/authorize?{query}"}

    @router.post("/businesses/{business_id}/accounts/manual-token")
    async def manual_token(
        business_id: UUID, body: ManualToken, service: Service, info: CheckedSession
    ) -> dict[str, Any]:
        try:
            account = await service.connect_manual(
                info.user.id, business_id, body.token, body.login
            )
        except BusinessNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Бизнес не найден") from None
        except LoginUnknown:
            raise HTTPException(
                422,
                "Не удалось узнать логин по токену. Впишите логин кабинета в Директе",
            ) from None
        except AccountTaken:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Этот кабинет уже подключён к другому бизнесу"
            ) from None
        return _account_view(account)

    @router.post("/businesses/{business_id}/accounts/connect")
    async def connect(
        business_id: UUID, service: Service, info: CheckedSession, then: str | None = None
    ) -> JSONResponse:
        try:
            url, state = await service.begin_connect(info.user.id, business_id)
        except BusinessNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Бизнес не найден") from None
        except ProviderUnavailable:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Подключение кабинета пока не настроено"
            ) from None
        response = JSONResponse({"authorize_url": url})
        response.set_cookie(
            FLOW_COOKIE,
            state,
            max_age=FLOW_COOKIE_SECONDS,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/api",
        )
        if then in AFTER_LOGIN:
            response.set_cookie(
                AFTER_LOGIN_COOKIE,
                then,
                max_age=FLOW_COOKIE_SECONDS,
                httponly=True,
                secure=secure,
                samesite="lax",
                path="/api",
            )
        return response

    @router.get("/accounts/yandex/callback")
    async def connect_callback(
        request: Request,
        service: Service,
        info: CurrentSession,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        back = AFTER_LOGIN.get(request.cookies.get(AFTER_LOGIN_COOKIE, ""), "/")
        if not _state_matches_browser(request, state) or state is None:
            logger.warning("OAuth connect state mismatch")
            return _home("connect=failed", back)
        try:
            if error is not None or code is None:
                await service.cancel_connect(state)
                logger.warning("OAuth connect denied or error: code_is_none=%s", code is None)
                return _home("connect=denied", back)
            await service.complete_connect(info.user.id, state, code)
        except AccountTaken:
            return _home("connect=taken", back)
        except (FlowInvalid, GrantRejected, BusinessNotFound) as exc:
            logger.warning("OAuth connect failed on complete_connect: %s", type(exc).__name__)
            return _home("connect=failed", back)
        except ProviderUnavailable as exc:
            logger.warning("OAuth connect provider unavailable: %s", type(exc).__name__)
            return _home("connect=unavailable", back)
        return _home("connect=ok", back)

    return router
