from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.business.domain.ports import ClaimedRead
from adhelper.business.domain.site import (
    FactField,
    ReadStatus,
    SiteFact,
    SiteRead,
    SiteRules,
    StoredRead,
)

READS = table(
    "site_reads",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("guest_key", Text),
    column("token_hash", Text),
    column("url", Text),
    column("status", Text),
    column("attempts", Integer),
    column("problem", Text),
    column("rules_version", Text),
    column("model", Text),
    column("facts", JSONB),
    column("dropped", JSONB),
    column("pages", JSONB),
    column("tokens_used", Integer),
    column("created_at", DateTime(timezone=True)),
    column("started_at", DateTime(timezone=True)),
    column("finished_at", DateTime(timezone=True)),
)
BUSINESSES = table("businesses", column("id", Uuid))
WAITING = (ReadStatus.QUEUED.value, ReadStatus.RUNNING.value)
DAY = timedelta(days=1)


# Одна блокировка на все гостевые просьбы: их мало, а лимиты общие.
GUEST_LOCK = 0x5173_0001


class TooManyReads(Exception):
    """Лимит разборов за сутки исчерпан."""


class TooManyGuestReads(Exception):
    """Исчерпан общий суточный потолок разборов до входа."""


def _fact_row(fact: SiteFact) -> dict[str, Any]:
    return {
        "field": fact.field.value,
        "value": fact.value,
        "quote": fact.quote,
        "page": fact.page,
        "price": fact.price,
    }


def _fact(raw: Mapping[str, Any]) -> SiteFact:
    return SiteFact(FactField(raw["field"]), raw["value"], raw["quote"], raw["page"], raw["price"])


class SqlSiteReads:
    """Разборы одного бизнеса. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def request(self, url: str, rules: SiteRules, now: datetime) -> int:
        """Ставит разбор в очередь или возвращает уже ждущий. Лимит — скользящие сутки."""
        mine = READS.c.business_id == self._business_id
        async with self._sessions.begin() as session:
            # Блокировка бизнеса: две одновременные просьбы не обойдут лимит.
            await session.execute(
                select(BUSINESSES.c.id)
                .where(BUSINESSES.c.id == self._business_id)
                .with_for_update()
            )
            waiting = await session.scalar(
                select(READS.c.id).where(mine, READS.c.status.in_(WAITING)).limit(1)
            )
            if waiting is not None:
                return int(waiting)
            used = await session.scalar(
                select(func.count()).where(mine, READS.c.created_at > now - DAY)
            )
            if (used or 0) >= rules.max_reads_per_day:
                raise TooManyReads
            new_id = await session.scalar(
                insert(READS)
                .values(
                    business_id=self._business_id,
                    url=url,
                    rules_version=rules.version,
                    created_at=now,
                )
                .returning(READS.c.id)
            )
        return int(new_id)

    async def latest(self) -> StoredRead | None:
        statement = (
            select(READS)
            .where(READS.c.business_id == self._business_id)
            .order_by(READS.c.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).mappings().first()
        return None if row is None else _stored(row)


class SqlGuestSiteReads:
    """Разборы до входа. Строку видит только тот, кто знает её ключ доступа."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def request(
        self, url: str, guest_key: str, token_hash: str, rules: SiteRules, now: datetime
    ) -> int:
        """Ставит разбор в очередь. Ждущие разборы тоже считаются: у гостя нет своей строки."""
        guests = READS.c.business_id.is_(None)
        recent = READS.c.created_at > now - DAY
        async with self._sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(GUEST_LOCK)))
            existing = await session.execute(
                select(READS.c.id)
                .where(
                    guests,
                    recent,
                    READS.c.guest_key == guest_key,
                    READS.c.url == url,
                    READS.c.status == ReadStatus.READY.value,
                )
                .order_by(READS.c.id.desc())
                .limit(1)
            )
            row = existing.first()
            if row is not None:
                await session.execute(
                    update(READS).where(READS.c.id == row.id).values(token_hash=token_hash)
                )
                return int(row.id)

            mine = await session.scalar(
                select(func.count()).where(guests, recent, READS.c.guest_key == guest_key)
            )
            if (mine or 0) >= rules.max_guest_reads_per_address:
                raise TooManyReads
            total = await session.scalar(select(func.count()).where(guests, recent))
            if (total or 0) >= rules.max_guest_reads_per_day:
                raise TooManyGuestReads
            new_id = await session.scalar(
                insert(READS)
                .values(
                    guest_key=guest_key,
                    token_hash=token_hash,
                    url=url,
                    rules_version=rules.version,
                    created_at=now,
                )
                .returning(READS.c.id)
            )
        return int(new_id)

    async def get(self, read_id: int, token_hash: str) -> StoredRead | None:
        statement = select(READS).where(
            READS.c.id == read_id,
            READS.c.business_id.is_(None),
            READS.c.token_hash == token_hash,
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).mappings().first()
        return None if row is None else _stored(row)


def _stored(row: RowMapping) -> StoredRead:
    return StoredRead(
        id=row["id"],
        url=row["url"],
        status=ReadStatus(row["status"]),
        problem=row["problem"],
        facts=tuple(_fact(item) for item in row["facts"]),
        pages=tuple(row["pages"]),
    )


class SqlSiteReadQueue:
    """Очередь разборов для воркера, поверх всех бизнесов."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(
        self, now: datetime, stale_before: datetime, max_attempts: int
    ) -> ClaimedRead | None:
        stale = and_(READS.c.status == ReadStatus.RUNNING.value, READS.c.started_at < stale_before)
        async with self._sessions.begin() as session:
            # Зависший разбор без попыток больше не берём: каждая попытка стоит вызова модели.
            await session.execute(
                update(READS)
                .where(stale, READS.c.attempts >= max_attempts)
                .values(status=ReadStatus.FAILED.value, problem="stale", finished_at=now)
            )
            row = (
                await session.execute(
                    select(READS.c.id, READS.c.url, READS.c.rules_version)
                    .where(or_(READS.c.status == ReadStatus.QUEUED.value, stale))
                    .order_by(READS.c.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).first()
            if row is None:
                return None
            await session.execute(
                update(READS)
                .where(READS.c.id == row.id)
                .values(
                    status=ReadStatus.RUNNING.value, attempts=READS.c.attempts + 1, started_at=now
                )
            )
            return ClaimedRead(row.id, row.url, row.rules_version)

    async def finish(self, read_id: int, result: SiteRead, model: str, now: datetime) -> None:
        # Только разбор в работе: повтор после сбоя не перепишет готовый результат.
        async with self._sessions.begin() as session:
            await session.execute(
                update(READS)
                .where(READS.c.id == read_id, READS.c.status == ReadStatus.RUNNING.value)
                .values(
                    status=result.status.value,
                    problem=None if result.problem is None else result.problem.value,
                    model=model or None,
                    facts=[_fact_row(fact) for fact in result.facts],
                    dropped=dict(result.dropped),
                    pages=list(result.pages),
                    tokens_used=result.tokens,
                    finished_at=now,
                )
            )
