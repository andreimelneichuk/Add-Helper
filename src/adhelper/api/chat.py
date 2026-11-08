"""Строка чата: перевод сообщения владельца в команду из закрытого набора.

Адрес ничего не меняет. Он отвечает, как понял просьбу, а выполняет её интерфейс после «да»
владельца теми же маршрутами, что и кнопки: политика, журнал, исполнитель.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.accounts.adapters.sql import account_states
from adhelper.accounts.application.service import AccountState
from adhelper.api.cabinet import OwnedChecked
from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.application.analytics import AdStatSummary, AnalyticsContext
from adhelper.commands.application.service import (
    CURRENT_RULES,
    Answer,
    CallBudget,
    interpret,
    known_from,
)
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.adapters.wizard import SqlWizardAds
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.overview import PLATFORM_ZONE, ads, platform_today, window


class CommandBody(BaseModel):
    # Длиннее правила всё равно попросят написать короче; предел защищает разбор и модель.
    text: str = Field(min_length=1, max_length=2000)


def _shown(answer: Answer) -> dict[str, Any]:
    command = answer.command
    return {
        "kind": command.kind.value,
        "rubles": command.rubles,
        "ad": asdict(command.ad) if command.ad else None,
        "service": command.service,
        "days": list(command.days),
        "wish": command.wish,
        "confirm": answer.confirm,
        "reply": answer.reply,
        "source": answer.source,
        "rules": answer.rules,
    }


def chat_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}/chat")

    @router.post("/command")
    async def post_command(
        request: Request, body: CommandBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
        now: datetime = request.app.state.clock()
        dashboard = SqlDashboard(sessions, business_id)
        lines = await ads(dashboard, window(platform_today(now), None, None))
        history_lines = await dashboard.history(limit=10)
        found = await SqlProfiles(sessions, business_id).get()
        chosen = await SqlWizardAds(sessions, business_id).chosen()
        accounts = await account_states(sessions, business_id)
        connected_logins = [login for login, state in accounts if state is AccountState.CONNECTED]
        connected_login = connected_logins[0] if connected_logins else None

        ad_pairs = [(f"{line.account}/{line.ad_id}", line.title) for line, _ in lines]
        if not ad_pairs and chosen:
            ad_pairs = [(f"draft/{i}", draft.headline) for i, draft in enumerate(chosen)]
        known = known_from(ad_pairs, found[0] if found else None)

        cost_micros = sum(line.totals.cost for line, _ in lines)
        impressions = sum(line.totals.impressions for line, _ in lines)
        clicks = sum(line.totals.clicks for line, _ in lines)
        conversions = sum(line.totals.conversions for line, _ in lines)
        ad_summaries = tuple(
            AdStatSummary(
                id=f"{line.account}/{line.ad_id}",
                title=line.title,
                impressions=line.totals.impressions,
                clicks=line.totals.clicks,
                conversions=line.totals.conversions,
                cost_micros=line.totals.cost,
                ctr_percent=((ratios.ctr_bp or 0) / 100.0) if ratios else 0.0,
            )
            for line, ratios in lines
        )
        recent_history = tuple(
            {
                "at": h.at.isoformat() if hasattr(h.at, "isoformat") else str(h.at),
                "explanation": h.explanation,
                "kind": h.kind,
                "source": h.source,
            }
            for h in history_lines
        )
        analytics_context = AnalyticsContext(
            period_label="За 7 дней",
            cost_micros=cost_micros,
            impressions=impressions,
            clicks=clicks,
            conversions=conversions,
            ads=ad_summaries,
            recent_history=recent_history,
            has_campaigns=bool(lines),
            connected_account=connected_login,
            wizard_ads=tuple(draft.headline for draft in chosen),
        )

        rules = load_rules(CURRENT_RULES)
        calls: CallBudget = request.app.state.command_calls
        day = now.astimezone(PLATFORM_ZONE).date().isoformat()
        answer = await interpret(
            body.text,
            known,
            rules,
            request.app.state.command_model,
            lambda: calls.take(str(business_id), day, rules.model_calls_per_day),
            analytics_context=analytics_context,
            langchain_agent=getattr(request.app.state, "langchain_agent", None),
        )
        return {**_shown(answer), "next": [_shown(more) for more in answer.more]}

    return router
