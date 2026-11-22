import contextlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
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
    exists,
    func,
    insert,
    or_,
    select,
    table,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.business.domain.tag import (
    MAX_COUNTERS,
    StoredTagCheck,
    TagResult,
    TagRules,
    TagStatus,
    normalize_manual_counters,
)

CHECKS = table(
    "tag_checks",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("url", Text),
    column("status", Text),
    column("attempts", Integer),
    column("counters", ARRAY(BigInteger)),
    column("counter_names", JSONB),
    column("city_counters", JSONB),
    column("default_city_id", Text),
    column("problem", Text),
    column("rules_version", Text),
    column("created_at", DateTime(timezone=True)),
    column("started_at", DateTime(timezone=True)),
    column("finished_at", DateTime(timezone=True)),
)
BUSINESSES = table("businesses", column("id", Uuid))
PROFILES = table(
    "business_profiles",
    column("business_id", Uuid),
    column("website", Text),
)
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
            prev = (
                await session.execute(
                    select(
                        CHECKS.c.counters,
                        CHECKS.c.counter_names,
                        CHECKS.c.city_counters,
                        CHECKS.c.default_city_id,
                    )
                    .where(
                        mine,
                        CHECKS.c.url == url,
                        CHECKS.c.status.in_((TagStatus.FOUND.value, TagStatus.MISSING.value)),
                    )
                    .order_by(CHECKS.c.created_at.desc(), CHECKS.c.id.desc())
                    .limit(1)
                )
            ).first()
            prev_counters = [int(n) for n in (prev.counters or [])] if prev is not None else []
            prev_names = (
                prev.counter_names
                if prev is not None and isinstance(prev.counter_names, dict)
                else {}
            )
            prev_city_counters = (
                prev.city_counters
                if prev is not None and isinstance(prev.city_counters, dict)
                else {}
            )
            new_id = await session.scalar(
                insert(CHECKS)
                .values(
                    business_id=self._business_id,
                    url=url,
                    counters=prev_counters,
                    counter_names=prev_names,
                    city_counters=prev_city_counters,
                    default_city_id=prev.default_city_id if prev is not None else None,
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
        return _to_stored(row)

    async def save_manual(
        self,
        url: str,
        counters: Sequence[int],
        counter_names: Mapping[int, str] | None,
        rules: TagRules,
        now: datetime,
        city_counters: Mapping[str, int] | None = None,
    ) -> StoredTagCheck:
        """Сохраняет список счётчиков (первый — основной) и их названия без сетевой проверки."""
        clean_counters, clean_names = normalize_manual_counters(counters, counter_names)
        status = TagStatus.FOUND if clean_counters else TagStatus.MISSING
        names_json = {str(k): v for k, v in clean_names.items()}
        mine = CHECKS.c.business_id == self._business_id
        async with self._sessions.begin() as session:
            await session.execute(
                select(BUSINESSES.c.id)
                .where(BUSINESSES.c.id == self._business_id)
                .with_for_update()
            )
            latest_row = (
                await session.execute(
                    select(CHECKS)
                    .where(mine)
                    .order_by(CHECKS.c.created_at.desc(), CHECKS.c.id.desc())
                    .limit(1)
                )
            ).first()
            kept_city_counters: dict[str, Any] = {}
            kept_city_id: str | None = None
            if latest_row is not None and latest_row.url == url:
                if isinstance(latest_row.city_counters, dict):
                    kept_city_counters = latest_row.city_counters
                kept_city_id = latest_row.default_city_id
            # Ручная правка счётчиков не должна стирать найденные счётчики городов.
            city_json = {str(k): int(v) for k, v in (city_counters or kept_city_counters).items()}
            if (
                latest_row is not None
                and latest_row.url == url
                and latest_row.status not in WAITING
            ):
                await session.execute(
                    update(CHECKS)
                    .where(mine, CHECKS.c.id == latest_row.id)
                    .values(
                        status=status.value,
                        counters=list(clean_counters),
                        counter_names=names_json,
                        city_counters=city_json,
                        problem=None,
                        finished_at=now,
                    )
                )
                target_id = int(latest_row.id)
            else:
                new_id = await session.scalar(
                    insert(CHECKS)
                    .values(
                        business_id=self._business_id,
                        url=url,
                        status=status.value,
                        counters=list(clean_counters),
                        counter_names=names_json,
                        city_counters=city_json,
                        default_city_id=kept_city_id,
                        problem=None,
                        rules_version=rules.version,
                        created_at=now,
                        finished_at=now,
                    )
                    .returning(CHECKS.c.id)
                )
                target_id = int(new_id or 0)
        return StoredTagCheck(
            id=target_id,
            url=url,
            status=status,
            counters=clean_counters,
            problem=None,
            finished_at=now,
            counter_names=clean_names,
            city_counters=city_json,
            default_city_id=kept_city_id,
        )

    async def counter(self) -> str | None:
        """Счётчик Метрики из последней проверки, нашедшей код: по нему читаем визиты."""
        async with self._sessions() as session:
            counters = await session.scalar(
                select(CHECKS.c.counters)
                .where(
                    CHECKS.c.business_id == self._business_id,
                    CHECKS.c.status == TagStatus.FOUND.value,
                )
                .order_by(CHECKS.c.finished_at.desc(), CHECKS.c.id.desc())
                .limit(1)
            )
        return str(counters[0]) if counters else None


class SqlTagCheckQueue:
    """Очередь проверок для воркера, поверх всех бизнесов."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def stale_sites(self, version: str) -> list[tuple[UUID, str]]:
        """Сайты, у которых нет проверки текущей версии и нет ждущей проверки."""
        current = exists(
            select(CHECKS.c.id).where(
                CHECKS.c.business_id == PROFILES.c.business_id,
                or_(CHECKS.c.status.in_(WAITING), CHECKS.c.rules_version == version),
            )
        )
        async with self._sessions() as session:
            rows = await session.execute(
                select(PROFILES.c.business_id, PROFILES.c.website)
                .where(PROFILES.c.website != "")
                .where(~current)
            )
            return [(row.business_id, str(row.website)) for row in rows]

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
            current = (
                await session.execute(
                    select(CHECKS.c.business_id, CHECKS.c.url)
                    .where(CHECKS.c.id == check_id, CHECKS.c.status == TagStatus.RUNNING.value)
                    .limit(1)
                )
            ).first()
            if current is None:
                return
            await session.execute(
                select(BUSINESSES.c.id)
                .where(BUSINESSES.c.id == current.business_id)
                .with_for_update()
            )
            prev = (
                await session.execute(
                    select(
                        CHECKS.c.id,
                        CHECKS.c.counters,
                        CHECKS.c.counter_names,
                        CHECKS.c.city_counters,
                        CHECKS.c.default_city_id,
                    )
                    .where(
                        CHECKS.c.business_id == current.business_id,
                        CHECKS.c.url == current.url,
                        CHECKS.c.id != check_id,
                        CHECKS.c.status.in_((TagStatus.FOUND.value, TagStatus.MISSING.value)),
                    )
                    .order_by(CHECKS.c.created_at.desc(), CHECKS.c.id.desc())
                    .limit(1)
                )
            ).first()
            merged_counters = list(result.counters)[:MAX_COUNTERS]
            merged_names: dict[str, str] = {}
            merged_city_counters: dict[str, int] = dict(result.city_counters)
            merged_city_id = result.default_city_id
            status = result.status
            target_ids = [check_id]
            if prev is not None:
                if int(prev.id) > check_id:
                    target_ids.append(int(prev.id))
                merged_city_id = merged_city_id or prev.default_city_id
                prev_counters = [int(n) for n in (prev.counters or [])]
                raw_names = prev.counter_names if isinstance(prev.counter_names, dict) else {}
                prev_city_counters = (
                    prev.city_counters if isinstance(prev.city_counters, dict) else {}
                )
                if result.status in (TagStatus.FOUND, TagStatus.MISSING) and prev_counters:
                    merged_counters = list(prev_counters)
                    for number in result.counters:
                        if number not in merged_counters:
                            merged_counters.append(number)
                    merged_counters = merged_counters[:MAX_COUNTERS]
                    if merged_counters:
                        status = TagStatus.FOUND
                merged_names = {
                    str(number): str(raw_names[str(number)])
                    for number in merged_counters
                    if str(number) in raw_names and str(raw_names[str(number)]).strip()
                }
                # Города только добавляются: пропавший с сайта город и его город по умолчанию
                # остаются в истории, как и счётчики в `counters`. Убрать их можно ручной правкой.
                if prev_city_counters:
                    merged_city_counters = {
                        **prev_city_counters,
                        **merged_city_counters,
                    }
            await session.execute(
                update(CHECKS)
                .where(
                    CHECKS.c.business_id == current.business_id,
                    CHECKS.c.id.in_(target_ids),
                )
                .values(
                    status=status.value,
                    counters=merged_counters,
                    counter_names=merged_names,
                    city_counters=merged_city_counters,
                    default_city_id=merged_city_id,
                    problem=result.problem if status is not TagStatus.FOUND else None,
                    finished_at=now,
                )
            )

    async def get_finished(self, check_id: int) -> tuple[UUID, str, tuple[int, ...]] | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(CHECKS.c.business_id, CHECKS.c.url, CHECKS.c.counters)
                    .where(CHECKS.c.id == check_id, CHECKS.c.status == TagStatus.FOUND.value)
                    .limit(1)
                )
            ).first()
        if row is None:
            return None
        return UUID(str(row.business_id)), str(row.url), tuple(int(c) for c in (row.counters or ()))


def _to_stored(row: Any) -> StoredTagCheck:
    counters = tuple(int(n) for n in (row.counters or ()))
    raw_names = row.counter_names if isinstance(row.counter_names, dict) else {}
    raw_city_counters = row.city_counters if isinstance(row.city_counters, dict) else {}
    names: dict[int, str] = {}
    for number in counters:
        val = raw_names.get(str(number))
        if isinstance(val, str) and val.strip():
            names[number] = val.strip()
    city_counters: dict[str, int] = {}
    for key, val in raw_city_counters.items():
        if isinstance(val, (int, str)):
            with contextlib.suppress(ValueError, TypeError):
                city_counters[str(key)] = int(val)
    return StoredTagCheck(
        id=int(row.id),
        url=str(row.url),
        status=TagStatus(row.status),
        counters=counters,
        problem=row.problem,
        finished_at=row.finished_at,
        counter_names=names,
        city_counters=city_counters,
        default_city_id=row.default_city_id,
    )
