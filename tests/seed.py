"""Минимальные владелец, бизнес и кабинет для тестов, которым нужны внешние ключи."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def seed_account(
    sessions: async_sessionmaker[AsyncSession], business_id: uuid.UUID, *logins: str
) -> None:
    user_id = uuid.uuid4()
    async with sessions.begin() as session:
        await session.execute(
            text("INSERT INTO users (id, yandex_uid) VALUES (:id, :uid)"),
            {"id": user_id, "uid": str(user_id)},
        )
        await session.execute(
            text("INSERT INTO businesses (id, user_id, name) VALUES (:id, :user, 'Тест')"),
            {"id": business_id, "user": user_id},
        )
        for login in logins:
            await session.execute(
                text(
                    "INSERT INTO ad_accounts (id, business_id, platform, external_login,"
                    " encrypted_access, encrypted_refresh, token_expires_at, scopes, state)"
                    " VALUES (:id, :business, 'fake', :login, 'x', 'x', :expires, '{}',"
                    " 'disconnected')"
                ),
                {
                    "id": uuid.uuid4(),
                    "business": business_id,
                    "login": login,
                    "expires": datetime(2030, 1, 1, tzinfo=UTC),
                },
            )
