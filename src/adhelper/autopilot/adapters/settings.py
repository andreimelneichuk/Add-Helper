"""Хранилище настроек автопилота. Каждый запрос ограничен business_id (изоляция по бизнесу)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    Text,
    Uuid,
    column,
    func,
    select,
    table,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.domain.decide import ActionKind
from adhelper.autopilot.domain.settings import AutopilotMode, AutopilotSettings, Cadence
from adhelper.autopilot.domain.trust import TrustClass

SETTINGS = table(
    "autopilot_settings",
    column("business_id", Uuid),
    column("mode", Text),
    column("allowed_actions", JSONB),
    column("cadence", Text),
    column("run_hour", Integer),
    column("envelope", JSONB),
    column("stopped_at", DateTime(timezone=True)),
    column("last_run_at", DateTime(timezone=True)),
    column("granted_classes", JSONB),
    column("granted_at", DateTime(timezone=True)),
    column("auto_since", DateTime(timezone=True)),
    column("updated_at", DateTime(timezone=True)),
)
LOG = table(
    "autopilot_settings_log",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("text", Text),
    column("created_at", DateTime(timezone=True)),
)
HISTORY_LIMIT = 20


@dataclass(frozen=True)
class SettingsLine:
    text: str
    at: datetime


def _settings(row: Any) -> AutopilotSettings:
    return AutopilotSettings(
        mode=AutopilotMode(row.mode),
        allowed_actions=frozenset(ActionKind(kind) for kind in row.allowed_actions),
        cadence=Cadence(row.cadence),
        run_hour=row.run_hour,
        envelope=dict(row.envelope),
        stopped_at=row.stopped_at,
        last_run_at=row.last_run_at,
        granted=frozenset(TrustClass(item) for item in row.granted_classes),
        granted_at=row.granted_at,
        auto_since=row.auto_since,
    )


def _values(settings: AutopilotSettings) -> dict[str, Any]:
    return {
        "mode": settings.mode.value,
        "allowed_actions": sorted(kind.value for kind in settings.allowed_actions),
        "cadence": settings.cadence.value,
        "run_hour": settings.run_hour,
        "envelope": dict(settings.envelope),
        "stopped_at": settings.stopped_at,
        "last_run_at": settings.last_run_at,
        "granted_classes": sorted(item.value for item in settings.granted),
        "granted_at": settings.granted_at,
        "auto_since": settings.auto_since,
    }


class SqlAutopilotSettings:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def get(self) -> AutopilotSettings | None:
        statement = select(SETTINGS).where(SETTINGS.c.business_id == self._business_id)
        async with self._sessions() as session:
            row = (await session.execute(statement)).one_or_none()
        return _settings(row) if row else None

    async def save(self, settings: AutopilotSettings, lines: Sequence[str]) -> None:
        """Настройки и строка истории пишутся вместе: нет строки без смены и смены без строки."""
        values = {"business_id": self._business_id, **_values(settings)}
        statement = insert(SETTINGS).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SETTINGS.c.business_id],
            set_={**_values(settings), "updated_at": func.now()},
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)
            for line in lines:
                await session.execute(LOG.insert().values(business_id=self._business_id, text=line))

    async def demote_from_auto(self, line: str) -> bool:
        """«Сам» → «Предлагает» одним условным UPDATE: гонки с другой правкой настроек нет.

        Остальные поля не читаются и не перезаписываются; строка истории пишется в той же
        транзакции и только если режим действительно сменился.
        """
        statement = (
            update(SETTINGS)
            .where(SETTINGS.c.business_id == self._business_id, SETTINGS.c.mode == "auto")
            .values(mode="confirm", auto_since=None, updated_at=func.now())
        )
        async with self._sessions() as session, session.begin():
            changed = getattr(await session.execute(statement), "rowcount", 0)
            if not changed:
                return False
            await session.execute(LOG.insert().values(business_id=self._business_id, text=line))
        return True

    async def mark_run(self, settings: AutopilotSettings, at: datetime) -> None:
        """Отметить разбор. Записи нет — она создаётся со значениями по умолчанию."""
        statement = insert(SETTINGS).values(
            business_id=self._business_id, **_values(settings) | {"last_run_at": at}
        )
        statement = statement.on_conflict_do_update(
            index_elements=[SETTINGS.c.business_id],
            set_={"last_run_at": at, "updated_at": func.now()},
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    async def history(self) -> Sequence[SettingsLine]:
        statement = (
            select(LOG.c.text, LOG.c.created_at)
            .where(LOG.c.business_id == self._business_id)
            .order_by(LOG.c.id.desc())
            .limit(HISTORY_LIMIT)
        )
        async with self._sessions() as session:
            return [SettingsLine(text, at) for text, at in await session.execute(statement)]


async def settings_of_businesses(
    sessions: async_sessionmaker[AsyncSession], business_ids: Sequence[UUID]
) -> dict[UUID, AutopilotSettings]:
    """Для воркера: сохранённые настройки нескольких бизнесов. Нет записи — нет ключа."""
    if not business_ids:
        return {}
    statement = select(SETTINGS).where(SETTINGS.c.business_id.in_(business_ids))
    async with sessions() as session:
        return {row.business_id: _settings(row) for row in await session.execute(statement)}
