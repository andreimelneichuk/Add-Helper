"""Экраны владельца: главная, реклама, статистика, объявления, история, профиль.

Каждый адрес начинается с бизнеса и проверяет, что он принадлежит вошедшему пользователю.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.accounts.application.service import BusinessNotFound
from adhelper.api.accounts import CheckedSession, CurrentSession, Service
from adhelper.budget.adapters.rules_file import load_rules as load_budget_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.watch_cycle import confirm_balance
from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.adapters.sql import SqlCreativeSets, SqlProfiles
from adhelper.creatives.application.service import (
    CURRENT_RULES,
    TooManySets,
    decide_draft,
    profile_from,
    request_set,
)
from adhelper.notifications.adapters.sql import SqlAlerts
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.overview import history, overview

NOT_FOUND = "Бизнес не найден"


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


class DecisionBody(BaseModel):
    decision: Literal["approved", "declined"]


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


def _set_view(stored: Any) -> dict[str, Any]:
    return asdict(stored)


def cabinet_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.get("/overview")
    async def get_overview(request: Request, business_id: Owned) -> dict[str, Any]:
        view = await overview(
            SqlDashboard(_sessions(request), business_id),
            _now(request),
            lambda version: load_budget_rules(version).balance_tolerance_bp,
        )
        result = asdict(view)
        result["campaigns"] = [
            {**asdict(line), "ratios": asdict(line_ratios)} for line, line_ratios in view.campaigns
        ]
        return result

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

    @router.get("/creative-sets")
    async def get_sets(request: Request, business_id: Owned) -> list[dict[str, Any]]:
        sets = SqlCreativeSets(_sessions(request), business_id)
        return [_set_view(stored) for stored in await sets.latest(20)]

    @router.post("/creative-sets", status_code=status.HTTP_202_ACCEPTED)
    async def post_set(request: Request, business_id: OwnedChecked) -> dict[str, int]:
        sets = SqlCreativeSets(_sessions(request), business_id)
        try:
            set_id = await request_set(sets, load_rules(CURRENT_RULES), _now(request))
        except TooManySets:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "На сегодня наборов достаточно"
            ) from None
        return {"id": set_id}

    @router.post("/creative-sets/{set_id}/drafts/{index}")
    async def post_decision(
        request: Request, set_id: int, index: int, body: DecisionBody, business_id: OwnedChecked
    ) -> dict[str, str]:
        sets = SqlCreativeSets(_sessions(request), business_id)
        if not await decide_draft(sets, set_id, index, body.decision == "approved"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        return {"decision": body.decision}

    return router
