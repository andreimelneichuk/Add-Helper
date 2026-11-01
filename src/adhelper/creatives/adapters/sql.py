from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    and_,
    column,
    func,
    or_,
    select,
    table,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.creatives.domain.model import (
    BusinessProfile,
    ClaimedSet,
    CreativeSet,
    CreativeSource,
    DraftDecision,
    Service,
    SetStatus,
    StoredDraft,
    StoredRejection,
    StoredSet,
)
from adhelper.db import Base

# Название берём из businesses: профиль хранит только то, что относится к рекламе.
BUSINESSES = table("businesses", column("id"), column("name"))
WAITING = (SetStatus.QUEUED.value, SetStatus.RUNNING.value)


class BusinessProfileRow(Base):
    __tablename__ = "business_profiles"

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", name="fk_business_profiles_business"), primary_key=True
    )
    # Цена услуги — факт профиля в целых рублях, а не деньги площадки.
    services: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    address: Mapped[str] = mapped_column(Text)
    features: Mapped[list[str]] = mapped_column(JSONB)
    niche: Mapped[str] = mapped_column(Text)
    website: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CreativeSetRow(Base):
    __tablename__ = "creative_sets"
    __table_args__ = (
        Index(
            "ix_creative_sets_waiting",
            "status",
            "created_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_creative_sets_business_id", "business_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", name="fk_creative_sets_business")
    )
    status: Mapped[str] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(Text)
    drafts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    rejected: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    notes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _profile(row: BusinessProfileRow, name: str) -> BusinessProfile:
    return BusinessProfile(
        name=name,
        services=tuple(Service(s["name"], s.get("price")) for s in row.services),
        address=row.address,
        features=tuple(row.features),
        niche=row.niche,
    )


def _stored(row: CreativeSetRow) -> StoredSet:
    return StoredSet(
        id=row.id,
        status=SetStatus(row.status),
        rules_version=row.rules_version,
        source=CreativeSource(row.source) if row.source else None,
        drafts=tuple(
            StoredDraft(d["headline"], d["text"], DraftDecision(d["decision"])) for d in row.drafts
        ),
        rejected=tuple(
            StoredRejection(r["headline"], r["text"], tuple(r["reasons"])) for r in row.rejected
        ),
        notes=tuple(row.notes),
        tokens_used=row.tokens_used,
        needs_review=row.needs_review,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


class SqlProfiles:
    """Профиль одного бизнеса. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def get(self) -> tuple[BusinessProfile, str] | None:
        statement = (
            select(BusinessProfileRow, BUSINESSES.c.name)
            .join(BUSINESSES, BUSINESSES.c.id == BusinessProfileRow.business_id)
            .where(BusinessProfileRow.business_id == self._business_id)
        )
        async with self._sessions() as session:
            found = (await session.execute(statement)).one_or_none()
        return (_profile(found[0], found[1]), found[0].website) if found else None

    async def save(self, profile: BusinessProfile, website: str) -> None:
        values = {
            "services": [asdict(service) for service in profile.services],
            "address": profile.address,
            "features": list(profile.features),
            "niche": profile.niche,
            "website": website,
            "updated_at": func.now(),
        }
        statement = (
            insert(BusinessProfileRow)
            .values(business_id=self._business_id, **values)
            .on_conflict_do_update(index_elements=["business_id"], set_=values)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)


class SqlProfileSource:
    """Профили для воркера: работает поверх всех бизнесов, пользователю не отдаётся."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def profile(self, business_id: UUID) -> BusinessProfile | None:
        found = await SqlProfiles(self._sessions, business_id).get()
        return found[0] if found else None


class SqlCreativeSets:
    """Наборы одного бизнеса. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def request(self, rules_version: str, now: datetime) -> int:
        row = CreativeSetRow(
            business_id=self._business_id,
            status=SetStatus.QUEUED.value,
            rules_version=rules_version,
            created_at=now,
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            return row.id

    async def count_since(self, since: datetime) -> int:
        statement = select(func.count()).where(
            CreativeSetRow.business_id == self._business_id, CreativeSetRow.created_at >= since
        )
        async with self._sessions() as session:
            return int(await session.scalar(statement) or 0)

    async def latest(self, limit: int) -> Sequence[StoredSet]:
        statement = (
            select(CreativeSetRow)
            .where(CreativeSetRow.business_id == self._business_id)
            .order_by(CreativeSetRow.id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            return [_stored(row) for row in await session.scalars(statement)]

    async def decide(self, set_id: int, index: int, decision: DraftDecision) -> bool:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(CreativeSetRow)
                .where(
                    CreativeSetRow.id == set_id,
                    CreativeSetRow.business_id == self._business_id,
                    CreativeSetRow.status == SetStatus.READY.value,
                )
                .with_for_update()
            )
            if row is None or not 0 <= index < len(row.drafts):
                return False
            drafts = [dict(d) for d in row.drafts]
            drafts[index]["decision"] = decision.value
            row.drafts = drafts
            return True


class SqlCreativeQueue:
    """Очередь наборов для воркера, поверх всех бизнесов."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(
        self, now: datetime, stale_before: datetime, max_attempts: int
    ) -> ClaimedSet | None:
        stale = and_(
            CreativeSetRow.status == SetStatus.RUNNING.value,
            CreativeSetRow.started_at < stale_before,
        )
        async with self._sessions.begin() as session:
            # Зависшие наборы без попыток больше не берём: каждая попытка стоит денег.
            await session.execute(
                update(CreativeSetRow)
                .where(stale, CreativeSetRow.attempts >= max_attempts)
                .values(status=SetStatus.FAILED.value, notes=["stale"], finished_at=now)
            )
            row = await session.scalar(
                select(CreativeSetRow)
                .where(or_(CreativeSetRow.status == SetStatus.QUEUED.value, stale))
                .order_by(CreativeSetRow.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = SetStatus.RUNNING.value
            row.attempts += 1
            row.started_at = now
            return ClaimedSet(row.id, row.business_id, row.rules_version)

    async def finish(self, set_id: int, result: CreativeSet, now: datetime) -> None:
        values = {
            "status": SetStatus.READY.value,
            "source": result.source.value,
            "drafts": [
                {"headline": d.headline, "text": d.text, "decision": DraftDecision.PROPOSED.value}
                for d in result.drafts
            ],
            "rejected": [
                {
                    "headline": r.draft.headline,
                    "text": r.draft.text,
                    "reasons": [v.kind.value for v in r.violations],
                }
                for r in result.rejected
            ],
            "notes": list(result.notes),
            "tokens_used": result.tokens_used,
            "needs_review": result.needs_review,
            "finished_at": now,
        }
        await self._update(set_id, values)

    async def fail(self, set_id: int, reason: str, now: datetime) -> None:
        await self._update(
            set_id, {"status": SetStatus.FAILED.value, "notes": [reason], "finished_at": now}
        )

    async def _update(self, set_id: int, values: dict[str, Any]) -> None:
        # Только набор в работе: повторный finish после сбоя не перезапишет готовый результат.
        async with self._sessions.begin() as session:
            await session.execute(
                update(CreativeSetRow)
                .where(CreativeSetRow.id == set_id, CreativeSetRow.status.in_(WAITING))
                .values(**values)
            )
