"""Экраны владельца: главная, реклама, статистика, объявления, история, профиль.

Каждый адрес начинается с бизнеса и проверяет, что он принадлежит вошедшему пользователю.
"""

from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.accounts.application.service import BusinessNotFound
from adhelper.api.accounts import CheckedSession, CurrentSession, Service
from adhelper.budget.adapters.rules_file import load_rules as load_budget_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.watch_cycle import confirm_balance
from adhelper.creatives.adapters.sql import SqlCreatives, SqlProfiles
from adhelper.creatives.application.service import (
    WrongState,
    board,
    confirm,
    creative_report,
    live_ad_context,
    profile_from,
    withdraw,
)
from adhelper.notifications.adapters.sql import SqlAlerts
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.overview import (
    BadPeriod,
    Window,
    ad_detail,
    ads,
    campaign_detail,
    history,
    overview,
    platform_today,
    window,
)

NOT_FOUND = "Бизнес не найден"
# Объявление другого бизнеса тоже даёт 409, а не 404: состояние чужих строк не раскрываем.
ALREADY_MOVED = "Объявление уже запущено, снято или его нет"


class ServiceItem(BaseModel):
    name: str = Field(max_length=100)
    # Цена услуги в рублях из профиля: это факт для объявлений, а не деньги площадки.
    price: int | None = Field(default=None, ge=0, le=10_000_000)


class ProfileBody(BaseModel):
    services: list[ServiceItem] = Field(max_length=30)
    address: str = Field(default="", max_length=300)
    features: list[str] = Field(default_factory=list, max_length=20)
    niche: str = Field(default="", max_length=100)
    website: str = Field(default="", max_length=300)


class ConfirmBody(BaseModel):
    account: str = Field(min_length=1, max_length=200)


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    return sessions


def _now(request: Request) -> datetime:
    now: datetime = request.app.state.clock()
    return now


async def _owned(business_id: UUID, service: Service, info: CurrentSession) -> UUID:
    try:
        await service.require_business(info.user.id, business_id)
    except BusinessNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    return business_id


async def _owned_checked(business_id: UUID, service: Service, info: CheckedSession) -> UUID:
    return await _owned(business_id, service, info)


Owned = Annotated[UUID, Depends(_owned)]
OwnedChecked = Annotated[UUID, Depends(_owned_checked)]
Day = Annotated[date | None, Query()]
BAD_PERIOD = "Период задан неверно: начало позже конца, конец в будущем или дольше года"


def _window(request: Request, since: Day = None, until: Day = None) -> Window:
    try:
        return window(platform_today(_now(request)), since, until)
    except BadPeriod:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_PERIOD) from None


Period = Annotated[Window, Depends(_window)]


def _with_ratios(pairs: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
    return [{**asdict(line), "ratios": asdict(line_ratios)} for line, line_ratios in pairs]


async def _profile(request: Request, business_id: UUID) -> Any:
    found = await SqlProfiles(_sessions(request), business_id).get()
    return found[0] if found else None


def cabinet_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.get("/overview")
    async def get_overview(request: Request, business_id: Owned, period: Period) -> dict[str, Any]:
        view = await overview(
            SqlDashboard(_sessions(request), business_id),
            _now(request),
            lambda version: load_budget_rules(version).balance_tolerance_bp,
            since=period.since,
            until=period.until,
        )
        result = asdict(view)
        result["campaigns"] = _with_ratios(view.campaigns)
        return result

    @router.get("/ads")
    async def get_ads(request: Request, business_id: Owned, period: Period) -> list[dict[str, Any]]:
        return _with_ratios(await ads(SqlDashboard(_sessions(request), business_id), period))

    @router.get("/campaigns/{account}/{campaign_id}")
    async def get_campaign(
        request: Request, account: str, campaign_id: str, business_id: Owned, period: Period
    ) -> dict[str, Any]:
        dashboard = SqlDashboard(_sessions(request), business_id)
        view = await campaign_detail(dashboard, period, account, campaign_id)
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Направление не найдено")
        result = asdict(view)
        result["ads"] = _with_ratios(view.ads)
        return result

    @router.get("/ads/{account}/{ad_id}")
    async def get_ad(
        request: Request, account: str, ad_id: str, business_id: Owned, period: Period
    ) -> dict[str, Any]:
        view = await ad_detail(
            SqlDashboard(_sessions(request), business_id), period, account, ad_id
        )
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        audience, basis = await live_ad_context(
            SqlCreatives(_sessions(request), business_id),
            await _profile(request, business_id),
            account,
            view.ad.campaign_id,
            view.ad.title,
            view.ad.text,
        )
        return {**asdict(view), "audience": asdict(audience), "basis": asdict(basis)}

    @router.get("/history")
    async def get_history(request: Request, business_id: Owned) -> list[dict[str, Any]]:
        lines = await history(SqlDashboard(_sessions(request), business_id))
        return [asdict(line) for line in lines]

    @router.post("/balance/confirm")
    async def post_confirm_balance(
        request: Request, body: ConfirmBody, business_id: OwnedChecked
    ) -> dict[str, bool]:
        sessions = _sessions(request)
        confirmed = await confirm_balance(
            SqlPeriodStore(sessions),
            SqlAlerts(sessions, business_id),
            business_id,
            body.account,
            _now(request),
        )
        if not confirmed:
            raise HTTPException(status.HTTP_409_CONFLICT, "Баланс счёта ещё не прочитан")
        return {"confirmed": True}

    @router.get("/profile")
    async def get_profile(request: Request, business_id: Owned) -> dict[str, Any]:
        found = await SqlProfiles(_sessions(request), business_id).get()
        if found is None:
            return {"services": [], "address": "", "features": [], "niche": "", "website": ""}
        profile, website = found
        return {**asdict(profile), "website": website}

    @router.put("/profile")
    async def put_profile(
        request: Request, body: ProfileBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        profile = profile_from(
            "",
            [(item.name, item.price) for item in body.services],
            body.address,
            body.features,
            body.niche,
        )
        profiles = SqlProfiles(_sessions(request), business_id)
        await profiles.save(profile, body.website.strip())
        return await get_profile(request, business_id)

    @router.get("/creatives")
    async def get_creatives(request: Request, business_id: Owned) -> dict[str, Any]:
        view = await board(SqlCreatives(_sessions(request), business_id))
        return {
            "creatives": [{**asdict(c), "campaign_name": name} for c, name in view.creatives],
            "writing": [{**asdict(w), "campaign_name": name} for w, name in view.writing],
        }

    @router.get("/creatives/{creative_id}")
    async def get_creative(
        request: Request, creative_id: int, business_id: Owned
    ) -> dict[str, Any]:
        report = await creative_report(
            SqlCreatives(_sessions(request), business_id),
            await _profile(request, business_id),
            creative_id,
        )
        if report is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        return asdict(report)

    @router.post("/creatives/{creative_id}/withdraw")
    async def post_withdraw(
        request: Request, creative_id: int, business_id: OwnedChecked
    ) -> dict[str, Any]:
        creatives = SqlCreatives(_sessions(request), business_id)
        try:
            await withdraw(creatives, creative_id, _now(request))
        except WrongState:
            raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_MOVED) from None
        return await get_creative(request, creative_id, business_id)

    @router.post("/creatives/{creative_id}/launch")
    async def post_launch(
        request: Request, creative_id: int, business_id: OwnedChecked
    ) -> dict[str, Any]:
        creatives = SqlCreatives(_sessions(request), business_id)
        try:
            await confirm(creatives, creative_id, _now(request))
        except WrongState:
            raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_MOVED) from None
        return await get_creative(request, creative_id, business_id)

    return router
