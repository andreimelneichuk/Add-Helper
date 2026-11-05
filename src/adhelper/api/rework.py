"""Переделка одного объявления по просьбе владельца (шаг 19б пути пользователя).

Просьба проверяется до модели: факта вне профиля в рекламе не будет. Варианты пишет воркер,
выбранный ждёт отправки в Директ; старое объявление работает, пока Яндекс проверяет новое.
"""

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from adhelper.api.cabinet import Owned, OwnedChecked, _now, _profile, _sessions
from adhelper.creatives.adapters.rules_file import load_rework_rules, load_rules
from adhelper.creatives.adapters.sql import ReworkStatus, SqlAdSlots, SqlReworks
from adhelper.creatives.application.rework import AskProblem, ask_rework
from adhelper.creatives.application.service import CURRENT_RULES

CURRENT_REWORK_RULES = "rework-1"
DAY = timedelta(hours=24)
PROBLEMS = {
    AskProblem.NO_AD: (status.HTTP_404_NOT_FOUND, "Объявление не найдено"),
    AskProblem.NO_PROFILE: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "В профиле нет услуг: переделывать не из чего. Заполните профиль в настройках",
    ),
    AskProblem.EMPTY: (status.HTTP_422_UNPROCESSABLE_CONTENT, "Напишите, что поменять"),
    AskProblem.TOO_LONG: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Просьба длиннее 200 символов: напишите коротко, что поменять",
    ),
    AskProblem.DAILY_LIMIT: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Сегодня тексты уже заказывали много раз. Попробуйте завтра",
    ),
}
FAILED = "Писатель сейчас не ответил, вариантов нет. Старое объявление работает. Попробуйте позже"
NOTHING_FIT = (
    "Все варианты писателя не прошли проверку фактов, показывать нечего."
    " Старое объявление работает. Попробуйте сформулировать просьбу иначе"
)
CHOSEN = (
    "Новый вариант поставлен в очередь. В Яндекс он уйдёт, когда сервис получит доступ к API;"
    " старое объявление работает, пока Яндекс проверяет новое, обычно до суток."
    " Отменить можно на экране «Объявления», пока вариант не отправлен"
)


class WishBody(BaseModel):
    wish: str = Field(max_length=1000)


class ChooseBody(BaseModel):
    creative_id: int


def _refusal(items: tuple[str, ...]) -> str:
    words = ", ".join(f"«{item}»" for item in items)
    return (
        f"В профиле нет {words}. В рекламу идут только факты из профиля:"
        " сначала добавьте это в профиль, потом попросите снова"
    )


def rework_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.post("/ads/{account}/{ad_id}/rework", status_code=status.HTTP_202_ACCEPTED)
    async def post_rework(
        request: Request, account: str, ad_id: str, body: WishBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions = _sessions(request)
        now = _now(request)
        rules = load_rules(CURRENT_RULES)
        asked = await ask_rework(
            SqlReworks(sessions, business_id),
            await _profile(request, business_id),
            account,
            ad_id,
            body.wish,
            rules,
            load_rework_rules(CURRENT_REWORK_RULES),
            await SqlAdSlots(sessions, business_id).sets_since(now - DAY),
            now,
        )
        if asked.problem is AskProblem.UNCONFIRMED:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _refusal(asked.items))
        if asked.problem is not None:
            code, text = PROBLEMS[asked.problem]
            raise HTTPException(code, text)
        return {"id": asked.set_id, "status": ReworkStatus.QUEUED.value}

    @router.get("/reworks/{set_id}")
    async def get_rework(request: Request, set_id: int, business_id: Owned) -> dict[str, Any]:
        view = await SqlReworks(_sessions(request), business_id).get(set_id)
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Переделка не найдена")
        problem = None
        if view.status is ReworkStatus.FAILED:
            problem = FAILED if "model_unavailable" in view.notes else NOTHING_FIT
        return {
            "id": set_id,
            "status": view.status.value,
            "ad_id": view.ad_id,
            "problem": problem,
            "variants": [
                {
                    "id": v.id,
                    "headline": v.headline,
                    "text": v.text,
                    "state": v.state.value,
                    "sources": [asdict(s) for s in v.sources],
                }
                for v in view.variants
            ],
        }

    @router.post("/reworks/{set_id}/choose")
    async def post_choose(
        request: Request, set_id: int, body: ChooseBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        reworks = SqlReworks(_sessions(request), business_id)
        view = await reworks.get(set_id)
        if view is None or body.creative_id not in {v.id for v in view.variants}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Вариант не найден")
        if not await reworks.choose(body.creative_id, _now(request)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Вариант уже выбран или снят")
        return {**(await get_rework(request, set_id, business_id)), "text": CHOSEN}

    return router
