"""Экономика бизнеса: сколько стоит клиент и какую цену обращения это допускает.

Цель считает код по правилам `economics-1`; без подтверждения владельца числа остаются
«ориентиром». Площадку этот маршрут не затрагивает. docs/17-autopilot-plan.md, этап 3.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from adhelper.api.cabinet import Owned, OwnedChecked, _now, _sessions
from adhelper.business.adapters.economics import SqlEconomics
from adhelper.business.adapters.rules_file import load_economics_rules
from adhelper.business.application.economics import (
    Economics,
    EconomicsError,
    Patch,
    View,
    build_view,
    reference,
    save_patch,
)
from adhelper.creatives.adapters.sql import SqlProfiles

FIELD_NAMES = {
    "lead_to_client_bp": "доля обращений, которые становятся клиентами",
    "margin_bp": "прибыль с чека",
    "repeat_bp": "повторные визиты",
    "ad_share_bp": "доля прибыли на рекламу",
    "capacity_per_week": "сколько клиентов в неделю вы принимаете",
    "avg_check": "средний чек",
}


class EconomicsPatch(BaseModel):
    avg_check_rubles: int | None = None
    lead_to_client_bp: int | None = None
    margin_bp: int | None = None
    repeat_bp: int | None = None
    ad_share_bp: int | None = None
    capacity_per_week: int | None = None
    confirm: bool = False


def _shown(view: View, economics: Economics) -> dict[str, Any]:
    return {
        "check_rubles": view.check_rubles,
        "check_from_profile": view.check_from_profile,
        "lead_to_client_bp": economics.lead_to_client_bp,
        "margin_bp": economics.margin_bp,
        "repeat_bp": economics.repeat_bp,
        "ad_share_bp": economics.ad_share_bp,
        "capacity_per_week": economics.capacity_per_week,
        # Пока владелец не подтвердил, число — ориентир.
        "confirmed": economics.confirmed,
        "goal_rubles": view.goal_rubles,
        "how": view.how,
        "services": [
            {"service": s.service, "price_rubles": s.price_rubles, "goal_rubles": s.goal_rubles}
            for s in view.services
        ],
    }


async def _view(request: Request, business_id: UUID) -> dict[str, Any]:
    sessions = _sessions(request)
    rules = load_economics_rules()
    stored = await SqlEconomics(sessions, business_id).get()
    economics = stored.economics if stored else reference(rules)
    found = await SqlProfiles(sessions, business_id).get()
    services = found[0].services if found else ()
    prices = [(s.name, s.price) for s in services if s.price]
    view = build_view(economics, stored.avg_check_micros if stored else None, prices)
    return _shown(view, economics)


def public_economics_router() -> APIRouter:
    """Ориентир до входа: шаг «Автопилот» мастера идёт раньше входа через Яндекс."""
    router = APIRouter(prefix="/api/economics")

    @router.get("/estimate")
    async def estimate(check: Annotated[int, Query(ge=1, le=1_000_000)]) -> dict[str, Any]:
        view = build_view(reference(load_economics_rules()), None, [("", check)])
        return {"check_rubles": check, "goal_rubles": view.goal_rubles, "how": view.how}

    return router


def economics_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}/economics")

    @router.get("")
    async def get_economics(request: Request, business_id: Owned) -> dict[str, Any]:
        return await _view(request, business_id)

    @router.put("")
    async def put_economics(
        request: Request, body: EconomicsPatch, business_id: OwnedChecked
    ) -> dict[str, Any]:
        store = SqlEconomics(_sessions(request), business_id)
        try:
            await save_patch(
                store, Patch(**body.model_dump()), load_economics_rules(), _now(request)
            )
        except EconomicsError as error:
            field = FIELD_NAMES.get(str(error), "значение")
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Проверьте число: {field}"
            ) from None
        return await _view(request, business_id)

    return router
