"""Наборы мастера до входа и выбор владельца после входа. Миграция 0018."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.creatives.adapters.sql import CreativeSetRow, profile_from_json, profile_to_json
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeSource,
    SetStatus,
    WizardRules,
)
from adhelper.creatives.domain.wizard import WizardDraft, WizardSet, with_sources

DAY = timedelta(hours=24)
# Лимит гостя считается под замком: два запроса подряд не проскочат оба.
GUEST_LOCK = 0x5173_0002


class TooManySets(Exception):
    """С этой сети за сутки наборов уже достаточно."""


class TooManyGuestSets(Exception):
    """Общий потолок гостей за сутки исчерпан."""


def _drafts(row: CreativeSetRow) -> tuple[AdDraft, ...]:
    return tuple(AdDraft(str(d["headline"]), str(d["text"])) for d in row.drafts or [])


def _view(row: CreativeSetRow) -> WizardSet:
    profile = profile_from_json(row.profile or {})
    return WizardSet(
        id=row.id,
        status=SetStatus(row.status),
        source=None if row.source is None else CreativeSource(row.source),
        drafts=with_sources(_drafts(row), profile),
        needs_review=row.needs_review,
        notes=tuple(row.notes or ()),
    )


class SqlWizardSets:
    """Наборы гостей. Строку видит только тот, кто знает её ключ доступа."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def request(
        self,
        profile: BusinessProfile,
        guest_key: str,
        token_hash: str,
        rules: WizardRules,
        creative_rules: str,
        now: datetime,
        force: bool = False,
    ) -> int:
        guests = CreativeSetRow.business_id.is_(None)
        recent = CreativeSetRow.created_at > now - DAY
        profile_json = profile_to_json(profile)
        async with self._sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(GUEST_LOCK)))
            # Кэширование: готовый набор этого гостя и профиля за сутки возвращаем повторно
            if not force:
                existing = await session.scalar(
                    select(CreativeSetRow)
                    .where(
                        guests,
                        recent,
                        CreativeSetRow.guest_key == guest_key,
                        CreativeSetRow.profile == profile_json,
                        CreativeSetRow.status == SetStatus.READY.value,
                    )
                    .order_by(CreativeSetRow.id.desc())
                    .limit(1)
                )
                if existing is not None:
                    existing.token_hash = token_hash
                    return existing.id

            mine = await session.scalar(
                select(func.count()).where(guests, recent, CreativeSetRow.guest_key == guest_key)
            )
            if (mine or 0) >= rules.max_guest_sets_per_address:
                raise TooManySets
            total = await session.scalar(select(func.count()).where(guests, recent))
            if (total or 0) >= rules.max_guest_sets_per_day:
                raise TooManyGuestSets
            row = CreativeSetRow(
                business_id=None,
                status=SetStatus.QUEUED.value,
                rules_version=creative_rules,
                reason="wizard",
                guest_key=guest_key,
                token_hash=token_hash,
                profile=profile_to_json(profile),
                created_at=now,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def get(self, set_id: int, token_hash: str) -> WizardSet | None:
        statement = select(CreativeSetRow).where(
            CreativeSetRow.id == set_id,
            CreativeSetRow.business_id.is_(None),
            CreativeSetRow.token_hash == token_hash,
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
        return None if row is None else _view(row)

    async def adopt(
        self, set_id: int, token_hash: str, business_id: UUID, kept: Sequence[int]
    ) -> bool:
        """Готовый набор переходит в бизнес с выбором владельца.

        Повтор тем же бизнесом безопасен: перенос после сбоя проходит ещё раз.
        """
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(CreativeSetRow)
                .where(
                    CreativeSetRow.id == set_id,
                    CreativeSetRow.token_hash == token_hash,
                    CreativeSetRow.profile.is_not(None),
                    CreativeSetRow.status == SetStatus.READY.value,
                    (CreativeSetRow.business_id.is_(None))
                    | (CreativeSetRow.business_id == business_id),
                )
                .values(business_id=business_id, guest_key=None, kept=list(kept))
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def ready(self, set_id: int, token_hash: str, business_id: UUID) -> WizardSet | None:
        """Набор, который этот бизнес может забрать: ещё гостевой или уже его."""
        statement = select(CreativeSetRow).where(
            CreativeSetRow.id == set_id,
            CreativeSetRow.token_hash == token_hash,
            CreativeSetRow.profile.is_not(None),
            (CreativeSetRow.business_id.is_(None)) | (CreativeSetRow.business_id == business_id),
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
        return None if row is None else _view(row)


class SqlWizardAds:
    """Объявления, которые владелец оставил в мастере. Запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def chosen(self) -> tuple[WizardDraft, ...]:
        statement = (
            select(CreativeSetRow)
            .where(
                CreativeSetRow.business_id == self._business_id,
                CreativeSetRow.kept.is_not(None),
            )
            .order_by(CreativeSetRow.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
        if row is None:
            return ()
        drafts = _view(row).drafts
        return tuple(drafts[i] for i in row.kept or [] if 0 <= i < len(drafts))
