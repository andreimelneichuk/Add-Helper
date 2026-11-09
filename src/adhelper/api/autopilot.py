"""Настройки автопилота: положение, расписание, разрешённые действия, остановка.

Здесь меняются только настройки в нашей базе. Площадка не затрагивается: решения о деньгах
по-прежнему принимает цикл автопилота по правилам. docs/17-autopilot-plan.md, этап 1.
"""

from datetime import UTC, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from adhelper.accounts.adapters.sql import logins_of_business
from adhelper.api.cabinet import Owned, OwnedChecked, _now, _sessions
from adhelper.autopilot.adapters.outcomes import SqlOutcomeStore
from adhelper.autopilot.adapters.settings import SqlAutopilotSettings
from adhelper.autopilot.application.settings import (
    DEFAULT_SETTINGS,
    ActionKind,
    AutoNotAllowed,
    AutopilotMode,
    AutopilotSettings,
    Cadence,
    Patch,
    SettingsError,
    next_run_at,
    stop,
    update_settings,
)
from adhelper.business.adapters.sql import SqlLeadGoal

AUTO_REFUSED = "Режим «Сам» ещё не разрешён: сейчас автопилот только следит или предлагает"


class SettingsPatch(BaseModel):
    mode: AutopilotMode | None = None
    allowed_actions: list[ActionKind] | None = Field(default=None, min_length=1)
    cadence: Cadence | None = None
    run_hour: int | None = None
    envelope: dict[str, int] | None = None
    resume: bool = False


def _auto_allowed(request: Request) -> bool:
    return bool(getattr(request.app.state, "autopilot_auto_allowed", False))


async def _status(
    request: Request, business_id: UUID, settings: AutopilotSettings, has_goal: bool
) -> str:
    if settings.stopped_at is not None:
        return "stopped"
    if not has_goal:
        return "no_goal"
    sessions = _sessions(request)
    logins = await logins_of_business(sessions, business_id)
    platform_for = request.app.state.platform_for
    if not any(platform_for(business_id, login) is not None for login in logins):
        return "not_connected"
    async with sessions() as session:
        untrusted = await session.scalar(
            text(
                "SELECT 1 FROM alerts WHERE business_id = :b AND kind = 'data_untrusted'"
                " AND resolved_at IS NULL LIMIT 1"
            ),
            {"b": business_id},
        )
    return "data_untrusted" if untrusted else "ok"


async def _view(request: Request, business_id: UUID) -> dict[str, Any]:
    sessions = _sessions(request)
    store = SqlAutopilotSettings(sessions, business_id)
    settings = await store.get() or DEFAULT_SETTINGS
    goal = await SqlLeadGoal(sessions, business_id).get()
    now = _now(request)
    following = next_run_at(settings, now)
    if following is not None:
        following = max(following, now)
    return {
        "mode": settings.mode.value,
        "allowed_actions": sorted(kind.value for kind in settings.allowed_actions),
        "cadence": settings.cadence.value,
        "run_hour": settings.run_hour,
        "envelope": dict(settings.envelope),
        "status": await _status(request, business_id, settings, bool(goal)),
        "next_run_at": following.astimezone(UTC).isoformat() if following else None,
        "last_run_at": settings.last_run_at.isoformat() if settings.last_run_at else None,
        "stopped_at": settings.stopped_at.isoformat() if settings.stopped_at else None,
        "auto_allowed": _auto_allowed(request),
        "history": [
            {"text": line.text, "at": line.at.isoformat()} for line in await store.history()
        ],
    }


def autopilot_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}/autopilot")

    @router.get("")
    async def get_autopilot(request: Request, business_id: Owned) -> dict[str, Any]:
        return await _view(request, business_id)

    @router.get("/report")
    async def get_report(request: Request, business_id: Owned) -> dict[str, Any]:
        """Что автопилот сделал за сутки и что получилось. Числа считает код, ничего не меняет."""
        sessions = _sessions(request)
        store = SqlOutcomeStore(sessions, business_id)
        since = _now(request) - timedelta(days=1)
        logins = await logins_of_business(sessions, business_id)
        done = 0
        outcomes: list[dict[str, Any]] = []
        for login in logins:
            done += await store.autopilot_changes_since(login, since)
            for row in await store.evaluated_since(login, since):
                outcomes.append(
                    {
                        "change_id": row.change_id,
                        "object_ref": row.object_ref,
                        "verdict": row.verdict,
                        "cpl_change_bp": row.cpl_change_bp,
                        "noise_bp": row.noise_bp,
                        "rolled_back": row.rollback_change_id is not None,
                        "note": row.note,
                        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
                    }
                )
        verdicts = [item["verdict"] for item in outcomes]
        return {
            "since": since.astimezone(UTC).isoformat(),
            "done": done,
            "better": verdicts.count("better"),
            "same": verdicts.count("same"),
            "worse": verdicts.count("worse"),
            "insufficient": verdicts.count("insufficient"),
            "rolled_back": sum(item["rolled_back"] for item in outcomes),
            "outcomes": outcomes,
        }

    @router.put("")
    async def put_autopilot(
        request: Request, body: SettingsPatch, business_id: OwnedChecked
    ) -> dict[str, Any]:
        store = SqlAutopilotSettings(_sessions(request), business_id)
        patch = Patch(
            mode=body.mode,
            allowed_actions=frozenset(body.allowed_actions) if body.allowed_actions else None,
            cadence=body.cadence,
            run_hour=body.run_hour,
            envelope=body.envelope,
            resume=body.resume,
        )
        try:
            await update_settings(store, patch, auto_allowed=_auto_allowed(request))
        except AutoNotAllowed:
            raise HTTPException(status.HTTP_409_CONFLICT, AUTO_REFUSED) from None
        except SettingsError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
        return await _view(request, business_id)

    @router.post("/stop")
    async def post_stop(request: Request, business_id: OwnedChecked) -> dict[str, Any]:
        await stop(SqlAutopilotSettings(_sessions(request), business_id), _now(request))
        return await _view(request, business_id)

    return router
