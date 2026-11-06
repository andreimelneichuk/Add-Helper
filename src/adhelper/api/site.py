"""Разбор сайта в черновик профиля: docs/12-agents.md §4, извлекатель.

Адрес проверяется здесь, скачивает и разбирает воркер. Черновик профиль не меняет:
владелец смотрит факты с цитатами и сохраняет профиль сам.
"""

import hashlib
import hmac
import ipaddress
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from adhelper.api.cabinet import Owned, OwnedChecked, _now, _sessions
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.adapters.site_reads import (
    SqlGuestSiteReads,
    SqlSiteReads,
    TooManyGuestReads,
    TooManyReads,
)
from adhelper.business.application.site import SiteRules, StoredRead, UrlProblem, check_url

URL_PROBLEMS = {
    UrlProblem.EMPTY: "Укажите адрес сайта",
    UrlProblem.TOO_LONG: "Адрес слишком длинный: укажите главную страницу сайта",
    UrlProblem.BAD_SCHEME: "Адрес сайта начинается с http:// или https://",
    UrlProblem.CREDENTIALS: "Уберите из адреса логин и пароль",
    UrlProblem.BAD_PORT: "Сайт на нестандартном порту не открываем: укажите обычный адрес",
    UrlProblem.NO_DOMAIN: "Это не похоже на адрес сайта. Пример: salon.ru",
    UrlProblem.PRIVATE: "Это адрес внутренней сети, такие не открываем. Укажите сайт из интернета",
}
READ_PROBLEMS = {
    "bad_url": "Сайт перенаправил на адрес, который мы не открываем. Заполните профиль вручную",
    "private": "Сайт ведёт во внутреннюю сеть, такие не открываем. Заполните профиль вручную",
    "unreachable": "Сайт не открылся. Проверьте адрес или заполните профиль вручную",
    "http_error": "Сайт ответил ошибкой. Проверьте адрес или заполните профиль вручную",
    "not_html": "По ссылке файл, а не страница сайта. Укажите адрес сайта",
    "too_many_redirects": "Сайт перенаправляет по кругу. Укажите другой адрес",
    "empty_page": "На странице почти нет текста: сайт, видимо, собирается скриптом."
    " Заполните профиль вручную",
    "no_model": "Разбор сайта сейчас выключен. Заполните профиль вручную",
    "model_unavailable": "Не получилось разобрать сайт. Попробуйте позже или заполните вручную",
    "stale": "Разбор не закончился. Попробуйте ещё раз",
}


class SiteBody(BaseModel):
    url: str = Field(max_length=2000)


def _checked_url(raw: str, rules: SiteRules) -> str:
    url, problem = check_url(raw, rules)
    if problem is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, URL_PROBLEMS[problem])
    return url


def _read_body(read: StoredRead) -> dict[str, Any]:
    return {
        "id": read.id,
        "url": read.url,
        "status": read.status.value,
        "problem": read.problem,
        "problem_text": None if read.problem is None else READ_PROBLEMS.get(read.problem),
        "facts": [
            {
                "field": fact.field.value,
                "value": fact.value,
                "price": fact.price,
                "quote": fact.quote,
                "page": fact.page,
            }
            for fact in read.facts
        ],
        "pages": list(read.pages),
    }


def site_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.post("/site-reads", status_code=status.HTTP_202_ACCEPTED)
    async def post_site_read(
        request: Request, body: SiteBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        rules = load_site_rules()
        url = _checked_url(body.url, rules)
        try:
            read_id = await SqlSiteReads(_sessions(request), business_id).request(
                url, rules, _now(request)
            )
        except TooManyReads:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Сегодня сайт разбирали уже {rules.max_reads_per_day} раз. Попробуйте завтра"
                " или заполните профиль вручную",
            ) from None
        return {"id": read_id, "status": "queued", "url": url}

    @router.get("/site-reads/latest")
    async def get_latest(request: Request, business_id: Owned) -> dict[str, Any] | None:
        read = await SqlSiteReads(_sessions(request), business_id).latest()
        return None if read is None else _read_body(read)

    return router


def _guest_key(request: Request) -> str:
    """Ключ лимита: сеть /24 или /64 под HMAC. Сам адрес нигде не хранится."""
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        network = host
    else:
        prefix = 24 if address.version == 4 else 64
        network = str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
    secret: bytes = request.app.state.guest_secret
    return hmac.new(secret, network.encode(), hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def guest_site_router() -> APIRouter:
    """Шаг 1 мастера до входа: docs/11-user-way.md, A1. Черновик видит только этот браузер."""
    router = APIRouter(prefix="/api/site-reads")

    # Тело только JSON: чужая страница не отправит его без предварительного запроса.
    @router.post("", status_code=status.HTTP_202_ACCEPTED)
    async def post_guest_read(request: Request, body: SiteBody) -> dict[str, Any]:
        rules = getattr(request.app.state, "site_rules", None) or load_site_rules()
        url = _checked_url(body.url, rules)
        token = secrets.token_urlsafe(32)
        try:
            read_id = await SqlGuestSiteReads(_sessions(request)).request(
                url, _guest_key(request), _token_hash(token), rules, _now(request)
            )
        except TooManyReads:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "С вашего устройства (IP-сети) сайт сегодня разбирали уже"
                f" {rules.max_guest_reads_per_address} раза."
                " Заполните профиль вручную или попробуйте завтра",
            ) from None
        except TooManyGuestReads:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Сейчас разбор сайта перегружен. Заполните профиль вручную или попробуйте позже",
            ) from None
        return {"id": read_id, "token": token, "status": "queued", "url": url}

    @router.get("/{read_id}")
    async def get_guest_read(
        request: Request,
        read_id: int,
        x_read_token: Annotated[str | None, Header(max_length=200)] = None,
    ) -> dict[str, Any]:
        read = None
        if x_read_token:
            read = await SqlGuestSiteReads(_sessions(request)).get(
                read_id, _token_hash(x_read_token)
            )
        if read is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Разбор не найден")
        return _read_body(read)

    return router
