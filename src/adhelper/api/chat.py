"""Строка чата: перевод сообщения владельца в команду из закрытого набора.

Адрес ничего не меняет. Он отвечает, как понял просьбу, а выполняет её интерфейс после «да»
владельца теми же маршрутами, что и кнопки: политика, журнал, исполнитель.
"""

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.accounts.adapters.sql import account_states
from adhelper.accounts.application.service import AccountState
from adhelper.api.cabinet import OwnedChecked
from adhelper.autopilot.adapters.settings import SqlAutopilotSettings
from adhelper.autopilot.application.settings import DEFAULT_SETTINGS, report_text
from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.application.analytics import AdStatSummary, AnalyticsContext
from adhelper.commands.application.service import (
    CURRENT_RULES,
    Answer,
    CallBudget,
    interpret,
    known_from,
)
from adhelper.commands.application.tool_agent import (
    HISTORY_MESSAGES,
    ChatTurn,
)
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.adapters.wizard import SqlWizardAds, WizardAdItem
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.facade import AdLine
from adhelper.stats.application.overview import PLATFORM_ZONE, Window, ads, platform_today, window


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2000)


class CommandBody(BaseModel):
    # Длиннее правила всё равно попросят написать короче; предел защищает разбор и модель.
    text: str = Field(min_length=1, max_length=2000)
    # Последние реплики переписки: агент помнит, о чём шла речь. Лишнее отбрасываем.
    history: list[HistoryItem] = Field(default_factory=list, max_length=40)


def _shown(answer: Answer, ttl: int) -> dict[str, Any]:
    command = answer.command
    return {
        "kind": command.kind.value,
        "rubles": command.rubles,
        "ad": asdict(command.ad) if command.ad else None,
        "service": command.service,
        "days": list(command.days),
        "cadence": command.cadence,
        "wish": command.wish,
        "confirm": answer.confirm,
        "reply": answer.reply,
        "source": answer.source,
        "rules": answer.rules,
        "expires_in": ttl,
    }


def _copy_key(title: str, text: str) -> tuple[str, str]:
    return (" ".join(title.lower().split()), " ".join(text.lower().split()))


def awaiting_review(wizard: Sequence[WizardAdItem], direct: Sequence[AdLine]) -> tuple[str, ...]:
    """Названия объявлений мастера на проверке Яндекса: как на экране «Реклама».

    Объявления Директа в состоянии «на проверке» считаются; копия мастера, что уже есть
    в Директе, и архивная — нет.
    """
    in_direct = {_copy_key(line.title, line.text) for line in direct}
    from_direct = tuple(line.title for line in direct if line.state == "moderation")
    return from_direct + tuple(
        item.headline
        for item in wizard
        if item.publication_status == "submitted"
        and not item.archived
        and _copy_key(item.headline, item.text) not in in_direct
    )


def summaries(lines: Sequence[tuple[AdLine, Any]]) -> tuple[AdStatSummary, ...]:
    return tuple(
        AdStatSummary(
            id=f"{line.account}/{line.ad_id}",
            title=line.title,
            impressions=line.totals.impressions,
            clicks=line.totals.clicks,
            conversions=line.totals.conversions,
            cost_micros=line.totals.cost,
            ctr_percent=((ratios.ctr_bp or 0) / 100.0) if ratios else 0.0,
            active=line.state in ("on", "moderation"),
        )
        for line, ratios in lines
    )


PERIOD_LABELS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "7d": "За 7 дней",
    "14d": "За 14 дней",
    "30d": "За 30 дней",
    "month": "С начала месяца",
}


def period_window(period: str, today: date) -> Window:
    """Окно статистики по названию периода: границы суток площадки, московские."""
    if period == "today":
        return window(today, today, today)
    if period == "yesterday":
        day = today - timedelta(days=1)
        return window(today, day, day)
    if period == "month":
        return window(today, today.replace(day=1), today)
    return window(today, None, None, days=int(period.removesuffix("d")))


def chat_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}/chat")

    @router.post("/command")
    async def post_command(
        request: Request, body: CommandBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
        now: datetime = request.app.state.clock()
        dashboard = SqlDashboard(sessions, business_id)
        today = platform_today(now)
        lines = await ads(dashboard, window(today, None, None, days=7))
        history_lines = await dashboard.history(limit=10)
        found = await SqlProfiles(sessions, business_id).get()
        all_wizard_ads = await SqlWizardAds(sessions, business_id).all_ads()
        accounts = await account_states(sessions, business_id)
        connected_logins = [login for login, state in accounts if state is AccountState.CONNECTED]
        connected_login = connected_logins[0] if connected_logins else None

        ad_pairs = [(f"{line.account}/{line.ad_id}", line.title) for line, _ in lines]
        if not ad_pairs and all_wizard_ads:
            ad_pairs = [(item.id, item.headline) for item in all_wizard_ads if item.active]
        known = known_from(ad_pairs, found[0] if found else None)

        cost_micros = sum(line.totals.cost for line, _ in lines)
        impressions = sum(line.totals.impressions for line, _ in lines)
        clicks = sum(line.totals.clicks for line, _ in lines)
        conversions = sum(line.totals.conversions for line, _ in lines)
        ad_summaries = summaries(lines)
        recent_history = tuple(
            {
                "at": h.at.isoformat() if hasattr(h.at, "isoformat") else str(h.at),
                "explanation": h.explanation,
                "kind": h.kind,
                "source": h.source,
            }
            for h in history_lines
        )
        open_periods = [p for p in await dashboard.periods(now) if p.starts_at <= now < p.ends_at]
        # Бюджет — число из «Обзора» и настроек: лимит месяца без вычета резерва.
        limit = sum(p.hard_limit for p in open_periods)
        spent_micros = sum(p.spent or 0 for p in open_periods)
        ap_store = SqlAutopilotSettings(sessions, business_id)
        ap_settings = await ap_store.get() or DEFAULT_SETTINGS
        ap_report = report_text(ap_settings, [line.text for line in await ap_store.history()])
        analytics_context = AnalyticsContext(
            period_label="За 7 дней",
            budget_rubles=limit // 1_000_000 if open_periods else None,
            spent_rubles=(spent_micros + 500_000) // 1_000_000 if open_periods else None,
            cost_micros=cost_micros,
            impressions=impressions,
            clicks=clicks,
            conversions=conversions,
            ads=ad_summaries,
            recent_history=recent_history,
            has_campaigns=bool(lines),
            connected_account=connected_login,
            wizard_ads=tuple(item.headline for item in all_wizard_ads if item.active),
            on_moderation=awaiting_review(all_wizard_ads, [line for line, _ in lines]),
            autopilot=ap_report,
        )

        async def stats_loader(period: str) -> tuple[str, tuple[AdStatSummary, ...]]:
            found_lines = await ads(dashboard, period_window(period, today))
            return PERIOD_LABELS[period], summaries(found_lines)

        history = [ChatTurn(item.role, item.text) for item in body.history[-HISTORY_MESSAGES:]]
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
            agent=getattr(request.app.state, "tool_agent", None),
            history=history,
            stats_loader=stats_loader,
        )
        ttl = rules.confirm_ttl_seconds
        return {**_shown(answer, ttl), "next": [_shown(more, ttl) for more in answer.more]}

    return router
