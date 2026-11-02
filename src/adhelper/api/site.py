"""Разбор сайта в черновик профиля: docs/12-agents.md §4, извлекатель.

Адрес проверяется здесь, скачивает и разбирает воркер. Черновик профиль не меняет:
владелец смотрит факты с цитатами и сохраняет профиль сам.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from adhelper.api.cabinet import Owned, OwnedChecked, _now, _sessions
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.adapters.site_reads import SqlSiteReads, TooManyReads
from adhelper.business.application.site import UrlProblem, check_url

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


def site_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.post("/site-reads", status_code=status.HTTP_202_ACCEPTED)
    async def post_site_read(
        request: Request, body: SiteBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        rules = load_site_rules()
        url, problem = check_url(body.url, rules)
        if problem is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, URL_PROBLEMS[problem])
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
        if read is None:
            return None
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

    return router
