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

from adhelper.api.cabinet import OwnedChecked
from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.application.service import (
    CURRENT_RULES,
    CallBudget,
    interpret,
    known_from,
)
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.stats.adapters.sql import SqlDashboard
from adhelper.stats.application.overview import PLATFORM_ZONE, ads, platform_today, window


class CommandBody(BaseModel):
    # Длиннее правила всё равно попросят написать короче; предел защищает разбор и модель.
    text: str = Field(min_length=1, max_length=2000)


def chat_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}/chat")

    @router.post("/command")
    async def post_command(
        request: Request, body: CommandBody, business_id: OwnedChecked
    ) -> dict[str, Any]:
        sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
        now: datetime = request.app.state.clock()
        lines = await ads(
            SqlDashboard(sessions, business_id), window(platform_today(now), None, None)
        )
        found = await SqlProfiles(sessions, business_id).get()
        known = known_from(
            [(f"{line.account}/{line.ad_id}", line.title) for line, _ in lines],
            found[0] if found else None,
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
        )
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

    return router
