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

from adhelper.creatives.domain.lifecycle import (
    WAITING as WAITING_CREATIVES,
)
from adhelper.creatives.domain.lifecycle import (
    Audience,
    CampaignInfo,
    CampaignSlots,
    Candidate,
    CreativeState,
    FactSource,
    HistoryEntry,
    NewCreative,
    SlotPlan,
    StoredCreative,
    WritingSet,
)
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    ClaimedSet,
    CreativeSet,
    Service,
    SetStatus,
)
from adhelper.db import Base

# Название берём из businesses: профиль хранит только то, что относится к рекламе.
BUSINESSES = table("businesses", column("id"), column("name"))
# Витрины площадки: их заполняет адаптер Директа (T6), здесь только чтение.
CAMPAIGNS = table(
    "campaigns",
    column("business_id"),
    column("account"),
    column("campaign_id"),
    column("name"),
    column("state"),
    column("targeting", JSONB),
)
ADS = table(
    "ads",
    column("business_id"),
    column("account"),
    column("campaign_id"),
    column("title"),
    column("text"),
    column("state"),
)
WAITING = (SetStatus.QUEUED.value, SetStatus.RUNNING.value)


class BusinessProfileRow(Base):
    __tablename__ = "business_profiles"

    business_id: Mapped[UUID] = mapped_column(primary_key=True)
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
    business_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(Text)
    drafts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    rejected: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    notes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    account: Mapped[str | None] = mapped_column(Text)
    campaign_id: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    wanted: Mapped[int] = mapped_column(Integer, server_default="0")
    judged: Mapped[bool] = mapped_column(Boolean, server_default="false")
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CreativeRow(Base):
    __tablename__ = "creatives"
    __table_args__ = (Index("ix_creatives_business_id_state", "business_id", "state"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID]
    set_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("creative_sets.id"))
    account: Mapped[str] = mapped_column(Text)
    campaign_id: Mapped[str] = mapped_column(Text)
    headline: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    auto_ok: Mapped[bool] = mapped_column(Boolean)
    # Почему написано: новое направление, изменился факт, освободилось место.
    reason: Mapped[str] = mapped_column(Text)
    facts: Mapped[list[str]] = mapped_column(JSONB)
    sources: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text)
    state_reason: Mapped[str] = mapped_column(Text)
    history: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _event(at: datetime, state: CreativeState, reason: str) -> dict[str, str]:
    return {"at": at.isoformat(), "state": state.value, "reason": reason}


def _creative(row: CreativeRow) -> StoredCreative:
    return StoredCreative(
        id=row.id,
        account=row.account,
        campaign_id=row.campaign_id,
        headline=row.headline,
        text=row.text,
        source=row.source,
        state=CreativeState(row.state),
        reason=row.reason,
        state_reason=row.state_reason,
        sources=tuple(FactSource(s["fragment"], s["label"], s["key"]) for s in row.sources),
        history=tuple(
            HistoryEntry(datetime.fromisoformat(h["at"]), h["state"], h["reason"])
            for h in row.history
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audience(raw: dict[str, Any]) -> Audience:
    def words(key: str) -> tuple[str, ...]:
        value = raw.get(key)
        return tuple(str(v) for v in value) if isinstance(value, list) else ()

    return Audience(
        placement=str(raw.get("placement", "")),
        regions=words("regions"),
        phrases=words("phrases"),
        schedule=str(raw.get("schedule", "")),
    )


def _profile(row: BusinessProfileRow, name: str) -> BusinessProfile:
    return BusinessProfile(
        name=name,
        services=tuple(Service(s["name"], s.get("price")) for s in row.services),
        address=row.address,
        features=tuple(row.features),
        niche=row.niche,
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


class SqlAdSlots:
    """Места одного бизнеса для хранителя объявлений. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def campaigns(self) -> Sequence[CampaignSlots]:
        own = CAMPAIGNS.c.business_id == self._business_id
        async with self._sessions() as session:
            campaigns = (
                await session.execute(
                    select(CAMPAIGNS.c.account, CAMPAIGNS.c.campaign_id, CAMPAIGNS.c.state)
                    .where(own)
                    .order_by(CAMPAIGNS.c.account, CAMPAIGNS.c.campaign_id)
                )
            ).all()
            ads = (
                await session.execute(
                    select(
                        ADS.c.account, ADS.c.campaign_id, ADS.c.title, ADS.c.text, ADS.c.state
                    ).where(ADS.c.business_id == self._business_id)
                )
            ).all()
            written = {
                (account, campaign_id)
                for account, campaign_id in await session.execute(
                    select(CreativeRow.account, CreativeRow.campaign_id)
                    .where(CreativeRow.business_id == self._business_id)
                    .distinct()
                )
            }
        result: list[CampaignSlots] = []
        for account, campaign_id, state in campaigns:
            own_ads = [a for a in ads if (a.account, a.campaign_id) == (account, campaign_id)]
            result.append(
                CampaignSlots(
                    account,
                    campaign_id,
                    running=state == "on",
                    live=tuple(AdDraft(a.title, a.text) for a in own_ads if a.state == "on"),
                    ever_had_ads=bool(own_ads) or (account, campaign_id) in written,
                )
            )
        return result

    async def candidates(self) -> Sequence[Candidate]:
        statement = select(CreativeRow).where(
            CreativeRow.business_id == self._business_id,
            CreativeRow.state.in_([s.value for s in WAITING_CREATIVES]),
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
        return [
            Candidate(
                row.id,
                row.account,
                row.campaign_id,
                CreativeState(row.state),
                AdDraft(row.headline, row.text),
                row.auto_ok,
                tuple(row.facts),
            )
            for row in rows
        ]

    async def open_sets(self) -> frozenset[tuple[str, str]]:
        statement = select(CreativeSetRow.account, CreativeSetRow.campaign_id).where(
            CreativeSetRow.business_id == self._business_id,
            CreativeSetRow.status.in_(WAITING),
            CreativeSetRow.account.is_not(None),
        )
        async with self._sessions() as session:
            return frozenset((str(a), str(c)) for a, c in await session.execute(statement))

    async def sets_since(self, since: datetime) -> int:
        statement = select(func.count()).where(
            CreativeSetRow.business_id == self._business_id, CreativeSetRow.created_at >= since
        )
        async with self._sessions() as session:
            return int(await session.scalar(statement) or 0)

    async def apply(self, plan: SlotPlan, creative_rules: str, now: datetime) -> None:
        """Переходы и заказы наборов в одной транзакции: строка набора и есть задание воркеру."""
        async with self._sessions.begin() as session:
            for move in plan.moves:
                # Двигаем только то, что ещё ждёт: владелец мог снять объявление за это время.
                row = await session.scalar(
                    select(CreativeRow)
                    .where(
                        CreativeRow.id == move.creative_id,
                        CreativeRow.business_id == self._business_id,
                        CreativeRow.state.in_([s.value for s in WAITING_CREATIVES]),
                    )
                    .with_for_update()
                )
                if row is None or row.state == move.state.value:
                    continue
                row.state = move.state.value
                row.state_reason = move.reason
                row.history = [*row.history, _event(now, move.state, move.reason)]
                row.updated_at = now
            for request in plan.requests:
                session.add(
                    CreativeSetRow(
                        business_id=self._business_id,
                        status=SetStatus.QUEUED.value,
                        rules_version=creative_rules,
                        created_at=now,
                        account=request.account,
                        campaign_id=request.campaign_id,
                        reason=request.reason.value,
                        wanted=request.wanted,
                    )
                )


class SqlKeeperBusinesses:
    """Бизнесы, у которых есть направления: только их объявления ведёт хранитель."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def business_ids(self) -> Sequence[UUID]:
        statement = select(CAMPAIGNS.c.business_id).distinct()
        async with self._sessions() as session:
            return [UUID(str(b)) for b in (await session.scalars(statement))]


class SqlCreatives:
    """Объявления одного бизнеса для экрана владельца. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def all(self) -> Sequence[StoredCreative]:
        statement = (
            select(CreativeRow)
            .where(CreativeRow.business_id == self._business_id)
            .order_by(CreativeRow.id.desc())
            .limit(200)
        )
        async with self._sessions() as session:
            return [_creative(row) for row in await session.scalars(statement)]

    async def get(self, creative_id: int) -> StoredCreative | None:
        statement = select(CreativeRow).where(
            CreativeRow.id == creative_id, CreativeRow.business_id == self._business_id
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
        return _creative(row) if row else None

    async def campaigns(self) -> dict[tuple[str, str], CampaignInfo]:
        statement = select(
            CAMPAIGNS.c.account,
            CAMPAIGNS.c.campaign_id,
            CAMPAIGNS.c.name,
            CAMPAIGNS.c.state,
            CAMPAIGNS.c.targeting,
        ).where(CAMPAIGNS.c.business_id == self._business_id)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return {
            (row.account, row.campaign_id): CampaignInfo(
                row.name, row.state == "on", _audience(row.targeting or {})
            )
            for row in rows
        }

    async def writing(self) -> Sequence[WritingSet]:
        statement = (
            select(CreativeSetRow)
            .where(
                CreativeSetRow.business_id == self._business_id,
                CreativeSetRow.status.in_(WAITING),
                CreativeSetRow.account.is_not(None),
            )
            .order_by(CreativeSetRow.id)
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
        return [
            WritingSet(
                row.id,
                str(row.account),
                str(row.campaign_id),
                str(row.reason),
                row.wanted,
                row.status,
                row.created_at,
            )
            for row in rows
        ]

    async def move(
        self,
        creative_id: int,
        allowed: Sequence[CreativeState],
        state: CreativeState,
        reason: str,
        now: datetime,
    ) -> bool:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(CreativeRow)
                .where(
                    CreativeRow.id == creative_id,
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.state.in_([s.value for s in allowed]),
                )
                .with_for_update()
            )
            if row is None:
                return False
            row.state = state.value
            row.state_reason = reason
            row.history = [*row.history, _event(now, state, reason)]
            row.updated_at = now
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
            name = None
            if row.account is not None:
                name = await session.scalar(
                    select(CAMPAIGNS.c.name).where(
                        CAMPAIGNS.c.business_id == row.business_id,
                        CAMPAIGNS.c.account == row.account,
                        CAMPAIGNS.c.campaign_id == row.campaign_id,
                    )
                )
            return ClaimedSet(
                row.id, row.business_id, row.rules_version, row.account, row.campaign_id, name
            )

    async def finish(
        self, set_id: int, result: CreativeSet, now: datetime, new: Sequence[NewCreative]
    ) -> None:
        values = {
            "status": SetStatus.READY.value,
            "source": result.source.value,
            "drafts": [{"headline": d.headline, "text": d.text} for d in result.drafts],
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
            "judged": result.judged,
            "finished_at": now,
        }
        # Результат и объявления в одной транзакции: повторный finish ничего не задвоит.
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(CreativeSetRow)
                .where(CreativeSetRow.id == set_id, CreativeSetRow.status.in_(WAITING))
                .with_for_update()
            )
            if row is None:
                return
            for key, value in values.items():
                setattr(row, key, value)
            if row.account is None or row.campaign_id is None:
                return
            for item in new:
                session.add(
                    CreativeRow(
                        business_id=row.business_id,
                        set_id=row.id,
                        account=row.account,
                        campaign_id=row.campaign_id,
                        headline=item.draft.headline,
                        text=item.draft.text,
                        source=result.source.value,
                        auto_ok=item.auto_ok,
                        reason=row.reason or "refill",
                        facts=[s.key for s in item.sources],
                        sources=[asdict(s) for s in item.sources],
                        state=CreativeState.READY.value,
                        state_reason="written",
                        history=[_event(now, CreativeState.READY, "written")],
                        created_at=now,
                        updated_at=now,
                    )
                )

    async def fail(self, set_id: int, reason: str, now: datetime) -> None:
        # Только набор в работе: повторный fail после сбоя не перезапишет готовый результат.
        async with self._sessions.begin() as session:
            await session.execute(
                update(CreativeSetRow)
                .where(CreativeSetRow.id == set_id, CreativeSetRow.status.in_(WAITING))
                .values(status=SetStatus.FAILED.value, notes=[reason], finished_at=now)
            )
