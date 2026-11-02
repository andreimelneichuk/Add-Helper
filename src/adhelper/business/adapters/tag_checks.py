from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    DateTime,
    Integer,
    Text,
    Uuid,
    and_,
    column,
    func,
    insert,
    or_,
    select,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.business.domain.tag import StoredTagCheck, TagResult, TagRules, TagStatus

CHECKS = table(
    "tag_checks",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("url", Text),
    column("status", Text),
    column("attempts", Integer),
    column("counters", ARRAY(BigInteger)),
    column("problem", Text),
    column("rules_version", Text),
    column("created_at", DateTime(timezone=True)),
    column("started_at", DateTime(timezone=True)),
    column("finished_at", DateTime(timezone=True)),
)
BUSINESSES = table("businesses", column("id", Uuid))
WAITING = (TagStatus.QUEUED.value, TagStatus.RUNNING.value)
DAY = timedelta(days=1)


class TooManyChecks(Exception):
    """Лимит проверок за сутки исчерпан."""


class SqlTagChecks:
    """Проверки кода одного бизнеса. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def request(self, url: str, rules: TagRules, now: datetime) -> int:
        mine = CHECKS.c.business_id == self._business_id
        async with self._sessions.begin() as session:
            await session.execute(
                select(BUSINESSES.c.id)
                .where(BUSINESSES.c.id == self._business_id)
                .with_for_update()
            )
            waiting = await session.scalar(
                select(CHECKS.c.id).where(mine, CHECKS.c.status.in_(WAITING)).limit(1)
            )
            if waiting is not None:
                return int(waiting)
            used = await session.scalar(
                select(func.count()).where(mine, CHECKS.c.created_at > now - DAY)
            )
            if (used or 0) >= rules.max_checks_per_day:
                raise TooManyChecks
            new_id = await session.scalar(
                insert(CHECKS)
                .values(
                    business_id=self._business_id,
                    url=url,
                    rules_version=rules.version,
                    created_at=now,
                )
                .returning(CHECKS.c.id)
            )
            return int(new_id or 0)

    async def latest(self) -> StoredTagCheck | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(CHECKS)
                    .where(CHECKS.c.business_id == self._business_id)
                    .order_by(CHECKS.c.created_at.desc(), CHECKS.c.id.desc())
                    .limit(1)
                )
            ).first()
        if row is None:
            return None
        return StoredTagCheck(
            row.id,
            row.url,
            TagStatus(row.status),
            tuple(int(n) for n in row.counters),
            row.problem,
            row.finished_at,
        )


class SqlTagCheckQueue:
    """Очередь проверок для воркера, поверх всех бизнесов."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(
        self, now: datetime, stale_before: datetime, max_attempts: int
    ) -> tuple[int, str] | None:
        stale = and_(CHECKS.c.status == TagStatus.RUNNING.value, CHECKS.c.started_at < stale_before)
        async with self._sessions.begin() as session:
            await session.execute(
                update(CHECKS)
                .where(stale, CHECKS.c.attempts >= max_attempts)
                .values(status=TagStatus.FAILED.value, problem="stale", finished_at=now)
            )
            row = (
                await session.execute(
                    select(CHECKS.c.id, CHECKS.c.url)
                    .where(or_(CHECKS.c.status == TagStatus.QUEUED.value, stale))
                    .order_by(CHECKS.c.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).first()
            if row is None:
                return None
            await session.execute(
                update(CHECKS)
                .where(CHECKS.c.id == row.id)
                .values(
                    status=TagStatus.RUNNING.value, attempts=CHECKS.c.attempts + 1, started_at=now
                )
            )
            return int(row.id), str(row.url)

    async def finish(self, check_id: int, result: TagResult, now: datetime) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(CHECKS)
                .where(CHECKS.c.id == check_id, CHECKS.c.status == TagStatus.RUNNING.value)
                .values(
                    status=result.status.value,
                    counters=list(result.counters),
                    problem=result.problem,
                    finished_at=now,
                )
            )
