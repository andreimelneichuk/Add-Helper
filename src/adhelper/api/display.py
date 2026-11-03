"""Где и когда показывать и желаемая цена обращения.

Район и часы меняют площадку, поэтому идут через журнал. Цель площадку не трогает:
это вход правил автопилота, каждое его решение записывает цель, с которой принято.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from adhelper.api.accounts import CheckedSession, Service
from adhelper.api.cabinet import Owned, OwnedChecked, _sessions
from adhelper.api.control import MICROS_PER_RUB, NO_ANSWER, NOT_CONNECTED, _ledger, _platform
from adhelper.business.adapters.sql import SqlLeadGoal
from adhelper.campaigns.adapters.rules_file import load_targeting_rules
from adhelper.campaigns.application.targeting import (
    Schedule,
    TargetingResult,
    TargetingRules,
    TargetingStatus,
    change_regions,
    change_schedule,
    describe_regions,
    describe_schedule,
    plan_skip,
    skip_days,
    summarize,
    vitrine_patch,
)
from adhelper.stats.adapters.sql import SqlDashboard


def _refusals(rules: TargetingRules) -> dict[str, str]:
    return {
        "no_days": "Выберите хотя бы один день",
        "bad_days": "Дни показа указаны неверно",
        "bad_hours": "Часы показа указаны неверно: начало должно быть раньше конца",
        "too_few_hours": f"Яндекс не принимает расписание короче {rules.min_weekly_hours}"
        " часов показа в неделю. Добавьте дни или часы",
        "no_regions": "Выберите хотя бы один регион",
        "unknown_region": "Такого региона нет в списке",
    }


NO_CAMPAIGNS = "Кампаний ещё нет: район и часы зададутся при запуске"
UNCONFIRMED = (
    "Яндекс подтвердил не всё. Где не подтвердил, осталось прежнее. Повторите, я начну с проверки"
)


class ScheduleBody(BaseModel):
    days: list[int] = Field(max_length=7)
    start: int
    end: int
    request_id: str = Field(min_length=1, max_length=100)


class SkipDaysBody(BaseModel):
    # Дни без показов: 0 — понедельник. Часы каждой кампании остаются её собственными.
    days: list[int] = Field(min_length=1, max_length=6)
    request_id: str = Field(min_length=1, max_length=100)


UNKNOWN_SCHEDULE = (
    "Часы показа направлений ещё не прочитаны у Яндекса, поэтому дни не меняю."
    " Задайте дни и часы в настройках показов"
)
TOO_SHORT = "Без этих дней у направления останется меньше {hours} часов показа в неделю, Яндекс"
TOO_SHORT += " такое не примет. Ничего не изменил"


class RegionsBody(BaseModel):
    regions: list[int] = Field(max_length=20)
    request_id: str = Field(min_length=1, max_length=100)


class GoalBody(BaseModel):
    # Желаемая цена обращения в целых рублях: так её вводит владелец.
    rubles: int = Field(ge=1, le=1_000_000)


async def _apply(
    request: Request,
    business_id: UUID,
    service: Service,
    info: CheckedSession,
    change: Any,
) -> dict[str, Any]:
    """Одна просьба для всех кампаний всех кабинетов бизнеса."""
    accounts = await service.accounts(info.user.id, business_id)
    if not accounts:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONNECTED)
    rules = load_targeting_rules()
    dashboard = SqlDashboard(_sessions(request), business_id)
    applied: list[str] = []
    failed: list[str] = []
    for account in accounts:
        campaigns = [cid for _, cid, _ in await dashboard.targetings(account.login)]
        if not campaigns:
            continue
        platform = _platform(request, business_id, account.login)
        ledger = _ledger(request, business_id, platform)
        result: TargetingResult = await change(ledger, account.login, campaigns, rules)
        if result.status is TargetingStatus.REFUSED:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, _refusals(rules)[result.problem or ""]
            )
        patch = vitrine_patch(result.planned, rules)
        for campaign_id in result.applied:
            await dashboard.note_targeting(account.login, campaign_id, patch)
        applied += [f"{account.login}:{cid}" for cid in result.applied]
        failed += [f"{account.login}:{cid}" for cid in result.failed]
    if not applied and not failed:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_CAMPAIGNS)
    if failed:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, UNCONFIRMED if applied else NO_ANSWER
        )
    return {"applied": applied}


def display_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.get("/display")
    async def get_display(request: Request, business_id: Owned) -> dict[str, Any]:
        rules = load_targeting_rules()
        dashboard = SqlDashboard(_sessions(request), business_id)
        summary = summarize([t for _, _, t in await dashboard.targetings()])
        goal = await SqlLeadGoal(_sessions(request), business_id).get()
        schedule = summary.schedule
        return {
            "campaigns": summary.campaigns,
            "schedule": None
            if schedule is None
            else {"days": list(schedule.days), "start": schedule.start, "end": schedule.end},
            "schedule_text": None if schedule is None else describe_schedule(schedule),
            "regions": None if summary.regions is None else list(summary.regions),
            "regions_text": None
            if summary.regions is None
            else describe_regions(summary.regions, rules),
            "goal": None if goal is None else goal // MICROS_PER_RUB,
            "catalog": [{"id": rid, "name": name} for rid, name in rules.regions.items()],
            "min_weekly_hours": rules.min_weekly_hours,
        }

    @router.post("/display/schedule")
    async def post_schedule(
        request: Request,
        body: ScheduleBody,
        business_id: OwnedChecked,
        service: Service,
        info: CheckedSession,
    ) -> dict[str, Any]:
        schedule = Schedule.of(body.days, body.start, body.end)

        async def change(ledger: Any, login: str, campaigns: list[str], rules: Any) -> Any:
            return await change_schedule(ledger, login, campaigns, schedule, rules, body.request_id)

        return await _apply(request, business_id, service, info, change)

    @router.post("/display/regions")
    async def post_regions(
        request: Request,
        body: RegionsBody,
        business_id: OwnedChecked,
        service: Service,
        info: CheckedSession,
    ) -> dict[str, Any]:
        async def change(ledger: Any, login: str, campaigns: list[str], rules: Any) -> Any:
            return await change_regions(
                ledger, login, campaigns, body.regions, rules, body.request_id
            )

        return await _apply(request, business_id, service, info, change)

    @router.post("/display/skip-days")
    async def post_skip_days(
        request: Request,
        body: SkipDaysBody,
        business_id: OwnedChecked,
        service: Service,
        info: CheckedSession,
    ) -> dict[str, Any]:
        """Не показывать в эти дни: каждое направление сохраняет свои часы.

        Сначала проверяем все кабинеты, потом меняем: просьба выполняется целиком или никак.
        """
        if any(not 0 <= day < 7 for day in body.days):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Дни указаны неверно")
        accounts = await service.accounts(info.user.id, business_id)
        if not accounts:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONNECTED)
        rules = load_targeting_rules()
        dashboard = SqlDashboard(_sessions(request), business_id)
        targetings = {
            account.login: [(cid, t) for _, cid, t in await dashboard.targetings(account.login)]
            for account in accounts
        }
        known = [(login, cid, t) for login, rows in targetings.items() for cid, t in rows]
        if not known:
            raise HTTPException(status.HTTP_409_CONFLICT, NO_CAMPAIGNS)
        # Проверка всех кабинетов до первого изменения.
        plan = plan_skip([(cid, t) for _, cid, t in known], body.days, rules)
        if plan.refused:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                TOO_SHORT.format(hours=rules.min_weekly_hours),
            )
        if not plan.change and not plan.unchanged:
            raise HTTPException(status.HTTP_409_CONFLICT, UNKNOWN_SCHEDULE)
        applied: list[str] = []
        failed: list[str] = []
        unknown: list[str] = []
        unchanged: list[str] = []
        for login, rows in targetings.items():
            if not rows:
                continue
            platform = _platform(request, business_id, login)
            ledger = _ledger(request, business_id, platform)
            result = await skip_days(ledger, login, rows, body.days, rules, body.request_id)
            for campaign_id, planned in result.planned.items():
                await dashboard.note_targeting(login, campaign_id, vitrine_patch(planned, rules))
            applied += [f"{login}:{cid}" for cid in result.applied]
            failed += [f"{login}:{cid}" for cid in result.failed]
            unknown += [f"{login}:{cid}" for cid in result.unknown]
            unchanged += [f"{login}:{cid}" for cid in result.unchanged]
        if failed:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, UNCONFIRMED if applied else NO_ANSWER
            )
        return {"applied": applied, "unknown": unknown, "unchanged": unchanged}

    @router.post("/goal")
    async def post_goal(
        request: Request, body: GoalBody, business_id: OwnedChecked
    ) -> dict[str, int]:
        await SqlLeadGoal(_sessions(request), business_id).set(body.rubles * MICROS_PER_RUB)
        return {"goal": body.rubles}

    return router
