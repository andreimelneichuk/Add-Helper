import logging
import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from adhelper.api.accounts import CheckedSession, Service
from adhelper.api.cabinet import Owned, OwnedChecked, Period, _now, _sessions
from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.emergency import StopReport, stop_account
from adhelper.budget.application.owner import (
    NOT_OWNER_PAUSE,
    BudgetStatus,
    ResumeReport,
    change_budget,
    resume_account,
)
from adhelper.budget.application.preflight import StoredPeriodPreflight
from adhelper.campaigns.adapters.rules_file import load_targeting_rules
from adhelper.campaigns.application.targeting import TARGETING_PREFIX, vitrine_patch
from adhelper.creatives.adapters.moderation_sql import SqlModeration
from adhelper.creatives.adapters.wizard import SqlWizardAds
from adhelper.creatives.application.moderation import follow_owner
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.consequences import consequence
from adhelper.ledger.application.owner import AD_PREFIX, set_ad_active
from adhelper.ledger.application.rollback import (
    Change,
    RollbackStatus,
    apply_request,
    preview_request,
    rollback_change,
)
from adhelper.ledger.application.service import Ledger
from adhelper.platforms.adapters.direct.transport import DirectAuthError, DirectError
from adhelper.platforms.adapters.gateway import AdvertiserSource, RoutingGateway
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.overview import ad_detail

MAX_CHANGE_ATTEMPTS = 3
NOT_CONNECTED = "Кабинет Яндекса пока не подключён, поэтому ничего не изменилось"
NO_ANSWER = "Яндекс не ответил. Ничего не изменилось, попробуйте позже"
PAUSED = "Поставил рекламу на паузу: вы попросили. Настройки сохранены."
RESUMED = "Снял паузу: вы попросили. Перед включением проверил, что бюджет выдержит."
MICROS_PER_RUB = 1_000_000
# Остановки, которые снимает не кнопка владельца, а решение после разбора причины.
GUARD_STOPS = {
    "budget_stopped": "Показы остановлены, потому что бюджет месяца заканчивается."
    " Снять эту остановку кнопкой нельзя: иначе деньги уйдут сверх бюджета",
    "budget_stop_pending": "Я пытаюсь остановить показы из-за бюджета."
    " Пока это не сделано, включать их нельзя",
    "balance_mismatch": "Показы остановлены, потому что счёт не сходится со статистикой."
    " Сначала подтвердите баланс, потом можно включать",
}
RESUME_REFUSAL = {
    NOT_OWNER_PAUSE: "Эти показы остановил не вы, а защита бюджета. Кнопка их не включит",
    "worst_case_exceeds_hard_limit": "Если включить, расход может выйти за бюджет месяца."
    " Увеличьте бюджет или дождитесь следующего месяца",
    "exceeds_operating_budget": "Лимиты направлений уже больше рабочей части бюджета."
    " Увеличьте бюджет, тогда можно включать",
    "period_ended": "Месяц бюджета закончился. Новый начнётся с первого числа",
    "no_active_period": "Бюджет месяца не задан. Задайте его, тогда можно включать",
    "budget_stop_pending": GUARD_STOPS["budget_stop_pending"],
}
BUDGET_UNCONFIRMED = (
    "Яндекс не подтвердил снижение лимитов. Бюджет пока прежний, попробуйте ещё раз"
)
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


class BudgetBody(BaseModel):
    # Бюджет месяца в целых рублях: так его вводит владелец.
    rubles: int = Field(ge=0, le=100_000_000)
    request_id: str = Field(min_length=1, max_length=100)


def _rubles(micros: int) -> str:
    whole = -(-micros // MICROS_PER_RUB)
    return f"{whole:,}".replace(",", "\u00a0") + "\u00a0₽"


def _ledger(
    request: Request,
    business_id: UUID,
    platform: Any,
    advertiser: AdvertiserSource | None = None,
) -> Ledger:
    sessions = _sessions(request)
    return Ledger(
        repository=SqlChangeRepository(sessions, business_id),
        gateway=RoutingGateway(
            PlatformChangeGateway(platform),
            platform,
            platform,
            launcher=platform,
            publisher=platform,
            registry=platform,
            advertiser=advertiser,
        ),
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


logger = logging.getLogger(__name__)
# Директ: приложению не одобрен доступ к API (docs/08-deployment.md §2, шаг 4). Это забота
# оператора, поэтому клиенту причина не показывается, она остаётся в логе.
APP_NOT_APPROVED = 58
BALANCE_APP_ACCESS = (
    "Остаток из Директа сейчас получить не удалось: мы уже разбираемся. "
    "Свой остаток можно посмотреть в кабинете Директа."
)
BALANCE_RECONNECT = "Яндекс не пускает: подключите кабинет заново в настройках."
BALANCE_NO_SHARED = (
    "У этого логина в Директе нет общего счёта, поэтому остаток через API не виден. "
    "Включите общий счёт в кабинете Директа или смотрите остаток там."
)
BALANCE_FAILED = "Директ сейчас не ответил. Остаток покажу, когда он снова будет доступен."


def _platform(request: Request, business_id: UUID, account: str) -> Any:
    platform = request.app.state.platform_for(business_id, account)
    if platform is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONNECTED)
    return platform


async def _ledger_for_change(
    request: Request, business_id: UUID, change_id: int
) -> tuple[Ledger, Change]:
    change = await SqlChangeRepository(_sessions(request), business_id).get(change_id)
    if change is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Изменение не найдено")
    platform = _platform(request, business_id, change.account)
    return _ledger(request, business_id, platform), change


async def _note_restored(
    request: Request, business_id: UUID, ledger: Ledger, changes: list[Change]
) -> None:
    """Витрина узнаёт прежние значения из снимка «до», без лишнего чтения площадки."""
    dashboard = SqlDashboard(_sessions(request), business_id)
    followed: set[str] = set()
    for change in changes:
        if change.target_ref.startswith(TARGETING_PREFIX):
            restored = {key: change.before[key] for key in change.planned}
            await dashboard.note_targeting(
                change.account,
                change.target_ref.removeprefix(TARGETING_PREFIX),
                vitrine_patch(restored, load_targeting_rules()),
            )
        if change.target_ref.startswith(AD_PREFIX):
            state = "on" if change.before.get("active") else "suspended"
            await dashboard.note_ad_state(
                change.account, change.target_ref.removeprefix(AD_PREFIX), state
            )
            followed.add(change.account)
    for account in sorted(followed):
        # Объявление сервиса возвращается на своё место и снова проходит чтение проверки.
        await follow_owner(
            SqlModeration(_sessions(request), business_id), ledger, account, _now(request)
        )


def control_router() -> APIRouter:
    """Действия владельца. Площадку меняет только журнал, здесь нет прямых вызовов."""
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.get("/accounts/{account}/balance")
    async def get_balance(
        request: Request, business_id: Owned, account: str, service: Service
    ) -> dict[str, Any]:
        """Остаток на счёте в Директе: чтение, площадку оно не меняет. Причина без остатка
        называется словами владельца, а не кодом ошибки."""
        platform = request.app.state.platform_for(business_id, account)
        if platform is None:
            return {"balance_micros": None, "problem": NOT_CONNECTED}
        try:
            micros = await platform.fetch_balance(account)
        except DirectAuthError as error:
            # Только тип причины, код и текст площадки: токена в них нет.
            logger.warning(
                "Остаток: отказ по доступу, код %s, причина %s: %s",
                error.code,
                type(error.__cause__).__name__,
                error,
            )
            if error.code == APP_NOT_APPROVED:
                return {"balance_micros": None, "problem": BALANCE_APP_ACCESS}
            return {"balance_micros": None, "problem": BALANCE_RECONNECT}
        except DirectError as error:
            logger.warning("Остаток: ошибка Директа, код %s: %s", error.code, error)
            if "shared account" in str(error):
                return {"balance_micros": None, "problem": BALANCE_NO_SHARED}
            return {"balance_micros": None, "problem": BALANCE_FAILED}
        if service is not None:
            await service.save_account_balance(business_id, account, micros, _now(request))
        return {"balance_micros": micros, "problem": None}

    @router.post("/ads/{account}/{ad_id}/state")
    async def post_ad_state(
        request: Request,
        account: str,
        ad_id: str,
        body: AdState,
        business_id: OwnedChecked,
        period: Period,
    ) -> dict[str, str]:
        target = account if (account.startswith("w") or account.startswith("draft")) else ad_id
        if target.startswith("draft/"):
            target = target.removeprefix("draft/")
        is_wizard_target = (
            account.startswith("w")
            or ad_id.startswith("w")
            or account == "draft"
            or "draft/" in ad_id
        )
        if is_wizard_target:
            wizard = SqlWizardAds(_sessions(request), business_id)
            idx_str = target.removeprefix("w")
            if idx_str.isdigit():
                ok = await wizard.set_active(int(idx_str), body.active)
                if ok:
                    return {"status": "applied"}
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        view = await ad_detail(
            SqlDashboard(_sessions(request), business_id), period, account, ad_id
        )
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")
        platform = _platform(request, business_id, account)
        ledger = _ledger(request, business_id, platform)
        done = await set_ad_active(
            ledger,
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
            # Снятое владельцем объявление сервиса освобождает место в направлении сразу.
            await follow_owner(
                SqlModeration(_sessions(request), business_id), ledger, account, _now(request)
            )
        return {"status": done}

    @router.post("/ads/{account}/{ad_id}/archive")
    async def post_ad_archive(
        request: Request,
        account: str,
        ad_id: str,
        business_id: OwnedChecked,
        period: Period,
    ) -> dict[str, str]:
        target = account if (account.startswith("w") or account.startswith("draft")) else ad_id
        if target.startswith("draft/"):
            target = target.removeprefix("draft/")
        is_wizard_target = (
            account.startswith("w")
            or ad_id.startswith("w")
            or account == "draft"
            or "draft/" in ad_id
        )
        if is_wizard_target:
            wizard = SqlWizardAds(_sessions(request), business_id)
            idx_str = target.removeprefix("w")
            if idx_str.isdigit():
                ok = await wizard.set_archived(int(idx_str), True)
                if ok:
                    return {"status": "applied"}
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")

        view = await ad_detail(
            SqlDashboard(_sessions(request), business_id), period, account, ad_id
        )
        if view is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Объявление не найдено")

        # Если объявление уже на площадке, сначала останавливаем показы через журнал (Ledger).
        # Используем динамический request_id, чтобы повтор не блокировался старой записью.
        platform = _platform(request, business_id, account)
        ledger = _ledger(request, business_id, platform)
        request_id = f"archive-{account}-{ad_id}-{secrets.token_urlsafe(12)}"
        done = await set_ad_active(
            ledger,
            account,
            ad_id,
            view.ad.title,
            False,
            request_id,
        )
        if done is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_ANSWER)
        if done == "failed":
            raise HTTPException(status.HTTP_409_CONFLICT, "Яндекс не принял это изменение")

        dashboard = SqlDashboard(_sessions(request), business_id)
        await dashboard.note_ad_state(account, ad_id, "archived")
        await follow_owner(
            SqlModeration(_sessions(request), business_id), ledger, account, _now(request)
        )
        return {"status": "applied"}

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

    @router.post("/resume")
    async def post_resume(
        request: Request,
        body: PauseBody,
        business_id: OwnedChecked,
        service: Service,
        info: CheckedSession,
    ) -> dict[str, list[str]]:
        accounts = await service.accounts(info.user.id, business_id)
        if not accounts:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONNECTED)
        dashboard = SqlDashboard(_sessions(request), business_id)
        for alert in await dashboard.alerts():
            if alert.kind in GUARD_STOPS:
                raise HTTPException(status.HTTP_409_CONFLICT, GUARD_STOPS[alert.kind])
        report = ResumeReport()
        for account in accounts:
            platform = _platform(request, business_id, account.login)
            already = len(report.resumed)
            await resume_account(
                account.login,
                platform,
                _ledger(request, business_id, platform),
                report,
                RESUMED,
                f"owner-resume:{body.request_id}",
            )
            for resumed in report.resumed[already:]:
                campaign_id = resumed.removeprefix(f"{account.login}:")
                await dashboard.note_campaign_state(account.login, campaign_id, "on")
        if report.failed and not report.resumed:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_ANSWER)
        if report.refused and not report.resumed:
            reason = report.refused[0][1]
            text = RESUME_REFUSAL.get(reason, "Включить не получилось: проверка бюджета не прошла")
            raise HTTPException(status.HTTP_409_CONFLICT, text)
        return {
            "resumed": report.resumed,
            "refused": [ref for ref, _ in report.refused],
            "failed": [ref for ref, _ in report.failed],
        }

    @router.post("/budget")
    async def post_budget(
        request: Request,
        body: BudgetBody,
        business_id: OwnedChecked,
        service: Service,
        info: CheckedSession,
    ) -> dict[str, str | int]:
        accounts = await service.accounts(info.user.id, business_id)
        if not accounts:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONNECTED)
        if len(accounts) > 1:
            # Как делить бюджет между кабинетами, владелец ещё не решал: не угадываем.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Подключено несколько кабинетов. Бюджет каждого пока меняется в Яндексе",
            )
        login = accounts[0].login
        platform = _platform(request, business_id, login)
        result = await change_budget(
            SqlPeriodStore(_sessions(request)),
            platform,
            _ledger(request, business_id, platform),
            business_id,
            login,
            body.rubles * MICROS_PER_RUB,
            _now(request),
        )
        if result.status is BudgetStatus.NO_PERIOD:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Бюджет месяца ещё не задан при запуске. Сначала запустите рекламу",
            )
        if result.status is BudgetStatus.BELOW_SPENT:
            minimum = _rubles(result.minimum or 0)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"В этом месяце уже потрачено больше. Бюджет можно снизить до {minimum}, не ниже",
            )
        if result.status is BudgetStatus.UNCONFIRMED:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, BUDGET_UNCONFIRMED)
        return {"status": result.status.value, "cuts": result.cuts}

    @router.get("/changes/{change_id}/rollback")
    async def get_rollback(request: Request, change_id: int, business_id: Owned) -> dict[str, Any]:
        """Что будет после отмены — до нажатия. Площадка только читается."""
        ledger, _change = await _ledger_for_change(request, business_id, change_id)
        preview = await preview_request(ledger, change_id)
        regions = load_targeting_rules().regions
        return {
            "possible": preview.result.status is RollbackStatus.READY,
            "status": preview.result.status.value,
            "text": consequence(preview.result, len(preview.ready), regions),
            "directions": len(preview.ready),
        }

    @router.post("/changes/{change_id}/rollback")
    async def post_rollback(
        request: Request, change_id: int, business_id: OwnedChecked, whole: bool = False
    ) -> dict[str, str]:
        ledger, change = await _ledger_for_change(request, business_id, change_id)
        if whole:
            preview = await preview_request(ledger, change_id)
            result = await apply_request(ledger, preview)
            changed = list(preview.ready)
        else:
            result = await rollback_change(ledger, change_id)
            changed = [change]
        if result.status in FINAL_REFUSAL:
            raise HTTPException(status.HTTP_409_CONFLICT, FINAL_REFUSAL[result.status])
        if result.status is RollbackStatus.APPLIED:
            await _note_restored(request, business_id, ledger, changed)
        return {"status": result.status.value}

    return router
