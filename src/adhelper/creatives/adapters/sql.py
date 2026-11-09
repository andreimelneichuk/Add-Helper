from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
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
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    and_,
    case,
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
    FactPlan,
    FactSource,
    HistoryEntry,
    LiveAd,
    NewCreative,
    Replacement,
    SlotPlan,
    StoredCreative,
    WriteReason,
    WritingSet,
)
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    ClaimedSet,
    CreativeSet,
    Rework,
    Service,
    SetStatus,
)
from adhelper.creatives.domain.rework import FACT_WISH
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
    column("ad_id"),
    column("campaign_id"),
    column("title"),
    column("text"),
    column("state"),
)
WAITING = (SetStatus.QUEUED.value, SetStatus.RUNNING.value)
# Состояния объявления в витрине, которые занимают место в направлении.
LIVE_AD_STATES = ("on", "moderation")
REJECTED_AD_STATE = "rejected"
# Причина набора и объявлений переделки по просьбе владельца.
REWORK_REASON = "rework"


class BusinessProfileRow(Base):
    __tablename__ = "business_profiles"

    business_id: Mapped[UUID] = mapped_column(primary_key=True)
    # Цена услуги — факт профиля в целых рублях, а не деньги площадки.
    services: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    address: Mapped[str] = mapped_column(Text)
    features: Mapped[list[str]] = mapped_column(JSONB)
    niche: Mapped[str] = mapped_column(Text)
    website: Mapped[str] = mapped_column(Text)
    target_region_id: Mapped[int | None] = mapped_column(BigInteger)
    target_city_name: Mapped[str | None] = mapped_column(Text)
    target_region_display: Mapped[str | None] = mapped_column(Text)
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
        Index(
            "ix_creative_sets_guest",
            "guest_key",
            "created_at",
            postgresql_where=text("business_id IS NULL"),
        ),
        Index(
            "ix_creative_sets_token",
            "token_hash",
            unique=True,
            postgresql_where=text("token_hash IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Набор мастера до входа живёт без бизнеса: миграция 0018.
    business_id: Mapped[UUID | None]
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
    guest_key: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    kept: Mapped[list[int] | None] = mapped_column(JSONB)
    rework: Mapped[dict[str, str] | None] = mapped_column(JSONB)


# Картинки объявлений: миграция 0023, хранилище в images_sql.py.
ACTIVE_IMAGE = "active"


class CreativeImageRow(Base):
    __tablename__ = "creative_images"
    __table_args__ = (
        UniqueConstraint("business_id", "sha256", name="uq_creative_images_sha256"),
        Index("ix_creative_images_business_state", "business_id", "state", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", name="fk_creative_images_business")
    )
    sha256: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    account: Mapped[str | None] = mapped_column(Text)
    campaign_id: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    prompt: Mapped[str | None] = mapped_column(Text)
    prompt_source: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(Text)
    cost_micro_usd: Mapped[int | None] = mapped_column(BigInteger)
    consent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    state: Mapped[str] = mapped_column(Text, server_default=ACTIVE_IMAGE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformImageRow(Base):
    __tablename__ = "platform_images"
    __table_args__ = (
        PrimaryKeyConstraint("business_id", "account", "sha256", name="pk_platform_images"),
        Index("ix_platform_images_hash", "business_id", "account", "image_hash"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", name="fk_platform_images_business"), primary_key=True
    )
    account: Mapped[str] = mapped_column(Text, primary_key=True)
    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    image_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CreativeRow(Base):
    __tablename__ = "creatives"
    __table_args__ = (
        Index("ix_creatives_business_id_state", "business_id", "state"),
        Index(
            "ix_creatives_ad_id",
            "business_id",
            "account",
            "ad_id",
            unique=True,
            postgresql_where=text("ad_id IS NOT NULL"),
        ),
    )

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
    replaces: Mapped[str | None] = mapped_column(Text)
    # Номер на площадке после отправки: миграция 0022.
    ad_id: Mapped[str | None] = mapped_column(Text)
    # Сколько раз цепочку чинили после отказа Яндекса и почему отклонено последнее.
    repairs: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    rejection: Mapped[str | None] = mapped_column(Text)
    # Картинка объявления: миграция 0023. Пусто — объявление без картинки.
    image_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("creative_images.id"))
    # Владелец явно попросил оставить замену без картинки; автоподбор её пропускает.
    image_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
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


def profile_to_json(profile: BusinessProfile) -> dict[str, Any]:
    return asdict(profile)


def profile_from_json(raw: dict[str, Any]) -> BusinessProfile:
    return BusinessProfile(
        name=str(raw.get("name", "")),
        services=tuple(Service(str(s["name"]), s.get("price")) for s in raw.get("services", [])),
        address=str(raw.get("address", "")),
        features=tuple(str(f) for f in raw.get("features", [])),
        niche=str(raw.get("niche", "")),
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
            # Keep a confirmed target when unrelated profile fields change. Changing the
            # address invalidates it, so launch must resolve the new address again.
            "target_region_id": None,
            "target_city_name": None,
            "target_region_display": None,
        }
        statement = (
            insert(BusinessProfileRow)
            .values(business_id=self._business_id, **values)
            .on_conflict_do_update(
                index_elements=["business_id"],
                set_={
                    **values,
                    "target_region_id": case(
                        (
                            BusinessProfileRow.address == profile.address,
                            BusinessProfileRow.target_region_id,
                        ),
                        else_=None,
                    ),
                    "target_city_name": case(
                        (
                            BusinessProfileRow.address == profile.address,
                            BusinessProfileRow.target_city_name,
                        ),
                        else_=None,
                    ),
                    "target_region_display": case(
                        (
                            BusinessProfileRow.address == profile.address,
                            BusinessProfileRow.target_region_display,
                        ),
                        else_=None,
                    ),
                },
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def save_target_region(self, region_id: int, city_name: str, display: str) -> None:
        """Persist the user's validated city choice for this business only."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(BusinessProfileRow)
                .where(BusinessProfileRow.business_id == self._business_id)
                .values(
                    target_region_id=region_id,
                    target_city_name=city_name,
                    target_region_display=display,
                    updated_at=func.now(),
                )
            )

    async def target_region(self) -> tuple[int, str, str] | None:
        async with self._sessions() as session:
            row = await session.get(BusinessProfileRow, self._business_id)
        if row is None or row.target_region_id is None:
            return None
        return (
            row.target_region_id,
            row.target_city_name or "",
            row.target_region_display or row.target_city_name or "",
        )


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
                    # Объявление на проверке Яндекса уже занимает место: иначе напишем лишнее.
                    live=tuple(
                        AdDraft(a.title, a.text) for a in own_ads if a.state in LIVE_AD_STATES
                    ),
                    ever_had_ads=bool(own_ads) or (account, campaign_id) in written,
                    held=sum(1 for a in own_ads if a.state == REJECTED_AD_STATE),
                )
            )
        return result

    async def candidates(self) -> Sequence[Candidate]:
        statement = select(CreativeRow).where(
            CreativeRow.business_id == self._business_id,
            CreativeRow.state.in_([s.value for s in WAITING_CREATIVES]),
            # Замены объявлений площадки не занимают новых мест: у каждой своё старое объявление.
            CreativeRow.replaces.is_(None),
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

    async def live_ads(self) -> Sequence[LiveAd]:
        statement = (
            select(ADS.c.account, ADS.c.campaign_id, ADS.c.ad_id, ADS.c.title, ADS.c.text)
            .where(ADS.c.business_id == self._business_id, ADS.c.state == "on")
            .order_by(ADS.c.account, ADS.c.campaign_id, ADS.c.ad_id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [LiveAd(str(a), str(c), str(i), AdDraft(t, x)) for a, c, i, t, x in rows]

    async def replacements(self) -> Sequence[Replacement]:
        statement = select(CreativeRow).where(
            CreativeRow.business_id == self._business_id,
            CreativeRow.state.in_([s.value for s in WAITING_CREATIVES]),
            CreativeRow.replaces.is_not(None),
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
        return [
            Replacement(
                row.id, str(row.replaces), CreativeState(row.state), AdDraft(row.headline, row.text)
            )
            for row in rows
        ]

    async def rewriting(self) -> frozenset[str]:
        """Объявления площадки, для которых набор переделки ещё пишется."""
        statement = select(CreativeSetRow.rework).where(
            CreativeSetRow.business_id == self._business_id,
            CreativeSetRow.status.in_(WAITING),
            CreativeSetRow.rework.is_not(None),
        )
        async with self._sessions() as session:
            return frozenset(str(r["ad_id"]) for r in await session.scalars(statement) if r)

    async def apply_facts(
        self, plan: FactPlan, creative_rules: str, rework_rules: str, now: datetime
    ) -> None:
        """Снятие устаревших замен и заказ новых в одной транзакции."""
        reason = WriteReason.FACT_CHANGED.value
        async with self._sessions.begin() as session:
            for creative_id in plan.stop:
                row = await session.scalar(
                    select(CreativeRow)
                    .where(
                        CreativeRow.id == creative_id,
                        CreativeRow.business_id == self._business_id,
                        CreativeRow.state.in_([s.value for s in WAITING_CREATIVES]),
                    )
                    .with_for_update()
                )
                if row is not None:
                    _set_state(row, CreativeState.STOPPED, reason, now)
            for ad in plan.rewrite:
                session.add(
                    CreativeSetRow(
                        business_id=self._business_id,
                        status=SetStatus.QUEUED.value,
                        rules_version=creative_rules,
                        created_at=now,
                        account=ad.account,
                        campaign_id=ad.campaign_id,
                        reason=reason,
                        wanted=1,
                        rework={
                            "ad_id": ad.ad_id,
                            "headline": ad.draft.headline,
                            "text": ad.draft.text,
                            "wish": FACT_WISH,
                            "rules": rework_rules,
                            "auto": True,
                        },
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
            .where(
                CreativeRow.business_id == self._business_id,
                # Невыбранные варианты переделки живут на экране объявления, а не в общем списке.
                ~and_(
                    CreativeRow.reason == REWORK_REASON,
                    CreativeRow.state_reason.in_(("written", "not_chosen")),
                ),
            )
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


def _rework(raw: dict[str, Any]) -> Rework:
    draft = AdDraft(raw["headline"], raw["text"])
    return Rework(raw["ad_id"], draft, raw["wish"], auto=bool(raw.get("auto")))


class ReworkStatus(StrEnum):
    QUEUED = "queued"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class ReworkView:
    status: ReworkStatus
    ad_id: str
    variants: tuple[StoredCreative, ...]
    notes: tuple[str, ...]


class SqlReworks:
    """Переделки одного бизнеса. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def live_ad(self, account: str, ad_id: str) -> tuple[str, AdDraft] | None:
        """Направление и текст объявления площадки, как его видит владелец."""
        statement = select(ADS.c.campaign_id, ADS.c.title, ADS.c.text).where(
            ADS.c.business_id == self._business_id,
            ADS.c.account == account,
            ADS.c.ad_id == ad_id,
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).first()
        return (str(row[0]), AdDraft(row[1], row[2])) if row else None

    async def request_photo_change(
        self, account: str, ad_id: str, image_id: int | None, now: datetime
    ) -> int | None:
        """Queue an identical-text replacement; the current ad stays live until approval."""
        async with self._sessions.begin() as session:
            ad = (
                await session.execute(
                    select(ADS.c.campaign_id, ADS.c.title, ADS.c.text, ADS.c.state).where(
                        ADS.c.business_id == self._business_id,
                        ADS.c.account == account,
                        ADS.c.ad_id == ad_id,
                    )
                )
            ).first()
            if ad is None or ad.state not in ("on", "moderation"):
                return None
            if image_id is not None:
                image_exists = await session.scalar(
                    select(CreativeImageRow.id).where(
                        CreativeImageRow.business_id == self._business_id,
                        CreativeImageRow.id == image_id,
                        CreativeImageRow.source == "owner",
                        CreativeImageRow.state == ACTIVE_IMAGE,
                    )
                )
                if image_exists is None:
                    raise ValueError("photo_not_available")
            pending = await session.scalar(
                select(CreativeRow.id)
                .where(
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.account == account,
                    CreativeRow.replaces == ad_id,
                    CreativeRow.reason == "owner_photo",
                    CreativeRow.state.in_(
                        (CreativeState.LAUNCH_PENDING.value, CreativeState.MODERATION.value)
                    ),
                )
                .order_by(CreativeRow.id.desc())
                .limit(1)
            )
            if pending is not None:
                return int(pending)
            old = await session.scalar(
                select(CreativeRow)
                .where(
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.account == account,
                    CreativeRow.ad_id == ad_id,
                )
                .order_by(CreativeRow.id.desc())
                .limit(1)
            )
            row = CreativeRow(
                business_id=self._business_id,
                set_id=None,
                account=account,
                campaign_id=str(ad.campaign_id),
                headline=str(ad.title),
                text=str(ad.text),
                source=old.source if old else "owner",
                auto_ok=True,
                reason="owner_photo",
                facts=list(old.facts) if old else [],
                sources=list(old.sources) if old else [],
                state=CreativeState.LAUNCH_PENDING.value,
                state_reason="owner_photo_change",
                history=[_event(now, CreativeState.LAUNCH_PENDING, "owner_photo_change")],
                replaces=ad_id,
                ad_id=None,
                repairs=0,
                rejection=None,
                image_id=image_id,
                image_disabled=image_id is None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def request(
        self,
        account: str,
        campaign_id: str,
        rework: Rework,
        rules_version: str,
        rework_version: str,
        variants: int,
        now: datetime,
    ) -> int:
        """Строка набора и есть задание воркеру: одна транзакция."""
        async with self._sessions.begin() as session:
            row = CreativeSetRow(
                business_id=self._business_id,
                status=SetStatus.QUEUED.value,
                rules_version=rules_version,
                account=account,
                campaign_id=campaign_id,
                reason=REWORK_REASON,
                wanted=variants,
                rework={
                    "ad_id": rework.ad_id,
                    "headline": rework.base.headline,
                    "text": rework.base.text,
                    "wish": rework.wish,
                    "rules": rework_version,
                },
                created_at=now,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def get(self, set_id: int) -> ReworkView | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(CreativeSetRow).where(
                    CreativeSetRow.id == set_id,
                    CreativeSetRow.business_id == self._business_id,
                    CreativeSetRow.rework.is_not(None),
                )
            )
            if row is None or row.rework is None:
                return None
            variants = await session.scalars(
                select(CreativeRow)
                .where(CreativeRow.set_id == set_id, CreativeRow.business_id == self._business_id)
                .order_by(CreativeRow.id)
            )
            stored = tuple(_creative(v) for v in variants)
        if row.status in WAITING:
            status = ReworkStatus.QUEUED
        elif row.status == SetStatus.READY.value and stored:
            status = ReworkStatus.READY
        else:
            status = ReworkStatus.FAILED
        return ReworkView(status, row.rework["ad_id"], stored, tuple(row.notes or ()))

    async def choose(self, creative_id: int, now: datetime) -> bool:
        """Выбранный вариант ждёт отправки в Директ, остальные из того же набора сняты."""
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(CreativeRow)
                .where(
                    CreativeRow.id == creative_id,
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.reason == REWORK_REASON,
                    CreativeRow.state == CreativeState.READY.value,
                )
                .with_for_update()
            )
            if row is None:
                return False
            siblings = await session.scalars(
                select(CreativeRow)
                .where(
                    CreativeRow.set_id == row.set_id,
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.id != row.id,
                    CreativeRow.state == CreativeState.READY.value,
                )
                .with_for_update()
            )
            for other in siblings:
                _set_state(other, CreativeState.WITHDRAWN, "not_chosen", now)
            _set_state(row, CreativeState.LAUNCH_PENDING, "owner_rework", now)
            return True


def _set_state(row: CreativeRow, state: CreativeState, reason: str, now: datetime) -> None:
    row.state = state.value
    row.state_reason = reason
    row.history = [*row.history, _event(now, state, reason)]
    row.updated_at = now


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
                row.id,
                row.business_id,
                row.rules_version,
                row.account,
                row.campaign_id,
                name,
                None if row.profile is None else profile_from_json(row.profile),
                None if row.rework is None else _rework(row.rework),
                None if row.rework is None else row.rework["rules"],
                max(1, row.wanted or 5),
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
            replaces = row.rework["ad_id"] if row.rework else None
            auto = bool(row.rework and row.rework.get("auto"))
            repairs = 0
            if row.rework and "repair" in row.rework:
                # Ремонт после отказа: заменяет то же, что заменяло отклонённое объявление.
                replaces = row.rework.get("replaces")
                repairs = int(row.rework["repair"])
            for item in new[:1] if auto else new:
                state, why = CreativeState.READY, "written"
                if auto:
                    # Замена сервиса: проверенный текст встаёт сам, иначе ждёт владельца.
                    state = (
                        CreativeState.LAUNCH_PENDING if item.auto_ok else CreativeState.NEEDS_OWNER
                    )
                    why = row.reason or WriteReason.FACT_CHANGED.value
                history = [_event(now, CreativeState.READY, "written")]
                if state is not CreativeState.READY:
                    history.append(_event(now, state, why))
                session.add(
                    CreativeRow(
                        business_id=row.business_id,
                        set_id=row.id,
                        account=row.account,
                        campaign_id=row.campaign_id,
                        headline=item.draft.headline,
                        text=item.draft.text,
                        source=result.source.value,
                        # Вариант переделки по просьбе ставит в показы только владелец.
                        auto_ok=item.auto_ok and (replaces is None or auto),
                        replaces=replaces,
                        repairs=repairs,
                        reason=row.reason or "refill",
                        facts=[s.key for s in item.sources],
                        sources=[asdict(s) for s in item.sources],
                        state=state.value,
                        state_reason=why,
                        history=history,
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
