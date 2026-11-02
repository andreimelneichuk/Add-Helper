from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from adhelper.api.accounts import CheckedSession, Service
from adhelper.api.cabinet import OwnedChecked, Period, _now, _sessions
from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.emergency import StopReport, stop_account
from adhelper.budget.application.preflight import StoredPeriodPreflight
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.owner import AD_PREFIX, set_ad_active
from adhelper.ledger.application.rollback import RollbackStatus, rollback_change
from adhelper.ledger.application.service import Ledger
from adhelper.platforms.adapters.gateway import RoutingGateway
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.overview import ad_detail

MAX_CHANGE_ATTEMPTS = 3
NOT_CONNECTED = "Кабинет Яндекса пока не подключён, поэтому ничего не изменилось"
NO_ANSWER = "Яндекс не ответил. Ничего не изменилось, попробуйте позже"
PAUSED = "Поставил рекламу на паузу: вы попросили. Настройки сохранены."
FINAL_REFUSAL = {
    RollbackStatus.NOT_APPLIED: "Это изменение не было применено, отменять нечего",
    RollbackStatus.NOT_REVERSIBLE: "Это действие нельзя отменить",
    RollbackStatus.ALREADY_REVERTED: "Это уже отменено",
    RollbackStatus.HAS_LATER_CHANGES: "После него были другие изменения. Сначала отмените их",
    RollbackStatus.CHANGED_OUTSIDE: "Это уже изменили в кабинете Яндекса, отмена его перезапишет",
    RollbackStatus.POLICY_REJECTED: "Отмена вышла бы за рамки бюджета, поэтому я её не выполнил",
    RollbackStatus.FAILED: "Яндекс не принял отмену. Прежнее значение осталось",
}


class AdState(BaseModel):
    active: bool
    # Повтор того же запроса не создаёт второе изменение.
    request_id: str = Field(min_length=1, max_length=100)


class PauseBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)


def _ledger(request: Request, business_id: UUID, platform: Any) -> Ledger:
    sessions = _sessions(request)
    return Ledger(
        repository=SqlChangeRepository(sessions, business_id),
        gateway=RoutingGateway(PlatformChangeGateway(platform), platform),
        lock=PostgresAccountLock(request.app.state.engine),
        max_attempts=MAX_CHANGE_ATTEMPTS,
        preflight=StoredPeriodPreflight(
            SqlPeriodStore(sessions),
            platform,
            business_id,
            lambda: _now(request),
            load_rules,
        ),
    )


def _platform(request: Request, business_id: UUID, account: str) -> Any:
    platform = request.app.state.platform_for(business_id, account)
    if platform is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONNECTED)
    return platform


def control_router() -> APIRouter:
    """Действия владельца. Площадку меняет только журнал, здесь нет прямых вызовов."""
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.post("/ads/{account}/{ad_id}/state")
    async def post_ad_state(
        request: Request,
        account: str,
        ad_id: str,
        body: AdState,
        business_id: OwnedChecked,
        period: Period,
    ) -> dict[str, str]:
        view = await ad_detail(
            SqlDashboard(_sessions(request), business_id), period, account, ad_id
        )
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        platform = _platform(request, business_id, account)
        done = await set_ad_active(
            _ledger(request, business_id, platform),
            account,
            ad_id,
            view.ad.title,
            body.active,
            body.request_id,
        )
        if done is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_ANSWER)
        if done == "failed":
            raise HTTPException(status.HTTP_409_CONFLICT, "Яндекс не принял это изменение")
        if done == "applied":
            dashboard = SqlDashboard(_sessions(request), business_id)
            await dashboard.note_ad_state(account, ad_id, "on" if body.active else "suspended")
        return {"status": done}

    @router.post("/pause")
    async def post_pause(
        request: Request,
        body: PauseBody,
        business_id: OwnedChecked,
        service: Service,
        info: CheckedSession,
    ) -> dict[str, list[str]]:
        report = StopReport()
        accounts = await service.accounts(info.user.id, business_id)
        if not accounts:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONNECTED)
        dashboard = SqlDashboard(_sessions(request), business_id)
        for account in accounts:
            platform = _platform(request, business_id, account.login)
            already = len(report.stopped)
            await stop_account(
                account.login,
                platform,
                _ledger(request, business_id, platform),
                report,
                PAUSED,
                f"owner:{body.request_id}",
            )
            # В отчёте строки вида «кабинет:кампания».
            for stopped in report.stopped[already:]:
                campaign_id = stopped.removeprefix(f"{account.login}:")
                await dashboard.note_campaign_state(account.login, campaign_id, "suspended")
        if not report.complete:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_ANSWER)
        return {"stopped": report.stopped}

    @router.post("/changes/{change_id}/rollback")
    async def post_rollback(
        request: Request, change_id: int, business_id: OwnedChecked
    ) -> dict[str, str]:
        change = await SqlChangeRepository(_sessions(request), business_id).get(change_id)
        if change is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Изменение не найдено")
        platform = _platform(request, business_id, change.account)
        result = await rollback_change(_ledger(request, business_id, platform), change_id)
        if result.status in FINAL_REFUSAL:
            raise HTTPException(status.HTTP_409_CONFLICT, FINAL_REFUSAL[result.status])
        if result.status is RollbackStatus.APPLIED and change.target_ref.startswith(AD_PREFIX):
            state = "on" if change.before.get("active") else "suspended"
            await SqlDashboard(_sessions(request), business_id).note_ad_state(
                change.account, change.target_ref.removeprefix(AD_PREFIX), state
            )
        return {"status": result.status.value}

    return router
