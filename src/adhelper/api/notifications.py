"""Куда приходят тревоги: почта из Яндекс ID и необязательный Telegram."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from adhelper.api.accounts import CheckedSession, CurrentSession
from adhelper.notifications.adapters.sql import SqlTelegramLinks
from adhelper.notifications.application.service import start_telegram_link


def _links(request: Request) -> SqlTelegramLinks:
    return SqlTelegramLinks(request.app.state.sessions)


def notifications_router() -> APIRouter:
    router = APIRouter(prefix="/api/me")

    @router.get("/notifications")
    async def channels(request: Request, info: CurrentSession) -> dict[str, Any]:
        bot: str = request.app.state.telegram_bot
        return {
            "email": info.user.email,
            "telegram": {
                "available": bool(bot),
                "linked": await _links(request).linked(info.user.id),
            },
        }

    @router.post("/telegram")
    async def link_telegram(request: Request, info: CheckedSession) -> dict[str, str]:
        bot: str = request.app.state.telegram_bot
        if not bot:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram пока не настроен")
        url = await start_telegram_link(
            _links(request), info.user.id, bot, request.app.state.clock()
        )
        return {"url": url}

    @router.delete("/telegram", status_code=status.HTTP_204_NO_CONTENT)
    async def unlink_telegram(request: Request, info: CheckedSession) -> None:
        await _links(request).unlink(info.user.id)

    return router
