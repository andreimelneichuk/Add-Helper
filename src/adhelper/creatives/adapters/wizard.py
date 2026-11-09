"""Наборы мастера до входа и выбор владельца после входа. Миграция 0018."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from adhelper.creatives.adapters.sql import CreativeSetRow, profile_from_json, profile_to_json
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeSource,
    SetStatus,
    WizardRules,
)
from adhelper.creatives.domain.wizard import FactSource, WizardDraft, WizardSet, with_sources
from adhelper.ledger.adapters.sql import ChangeRow
from adhelper.ledger.domain.model import ChangeKind, ChangeStatus

DAY = timedelta(hours=24)
# Лимит гостя считается под замком: два запроса подряд не проскочат оба.
GUEST_LOCK = 0x5173_0002
WIZARD_AD_LOCK = 0x5173_0004


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
        wanted: int = 5,
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
                        CreativeSetRow.wanted == wanted,
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
                wanted=wanted,
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
                .values(
                    business_id=business_id,
                    guest_key=None,
                    kept=list(kept),
                    created_at=func.now(),
                )
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


@dataclass(frozen=True)
class WizardAdItem:
    id: str
    index: int
    headline: str
    text: str
    facts: tuple[str, ...]
    active: bool
    services: tuple[str, ...] = ()
    archived: bool = False
    submitted: bool = False


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
                CreativeSetRow.reason == "wizard",
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
        raw = row.drafts or []
        chosen_items: list[WizardDraft] = []
        for i in row.kept or []:
            if 0 <= i < len(drafts) and not (i < len(raw) and bool(raw[i].get("archived", False))):
                d = drafts[i]
                raw_d = raw[i]
                if not d.sources and raw_d.get("sources"):
                    custom_sources = tuple(
                        FactSource(
                            fragment=s.get("quote", ""),
                            label=s.get("label", "Ваш текст"),
                            key="custom",
                        )
                        for s in raw_d.get("sources", [])
                    )
                    d = WizardDraft(d.headline, d.text, sources=custom_sources)
                chosen_items.append(d)
        return tuple(chosen_items)

    async def all_ads(self) -> list[WizardAdItem]:
        statement = (
            select(CreativeSetRow)
            .where(
                CreativeSetRow.business_id == self._business_id,
                CreativeSetRow.reason == "wizard",
            )
            .order_by(CreativeSetRow.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
            if row is None or not row.drafts:
                return []
            confirmed = (
                await session.scalars(
                    select(ChangeRow.planned).where(
                        ChangeRow.business_id == self._business_id,
                        ChangeRow.created_at >= row.created_at,
                        ChangeRow.kind.in_(
                            (ChangeKind.CREATE_CAMPAIGN.value, ChangeKind.CREATE_AD.value)
                        ),
                        ChangeRow.status == ChangeStatus.APPLIED.value,
                    )
                )
            ).all()
        submitted = Counter(
            (str(ad["headline"]), str(ad["text"]))
            for planned in confirmed
            for ad in planned.get("ads", [planned])
            if "headline" in ad and "text" in ad
        )
        view = _view(row)
        drafts = view.drafts
        kept_set = set(row.kept or [])
        submitted_remaining = submitted.copy()
        items: list[WizardAdItem] = []
        for i, d in enumerate(drafts):
            raw = row.drafts[i] if i < len(row.drafts) else {}
            is_archived = bool(raw.get("archived", False))
            is_active = (i in kept_set) and not is_archived
            is_off = bool(raw.get("off", False))
            if not is_active and not is_archived and not is_off:
                continue
            raw_sources = tuple(s.get("label", "") for s in raw.get("sources", []))
            facts = tuple(source.label for source in d.sources) or raw_sources or ("Ваш текст",)
            text_key = (d.headline, d.text)
            is_submitted = submitted_remaining[text_key] > 0
            if is_submitted:
                submitted_remaining[text_key] -= 1
            items.append(
                WizardAdItem(
                    id=f"w{i}",
                    index=i,
                    headline=d.headline,
                    text=d.text,
                    facts=facts,
                    services=tuple(
                        dict.fromkeys(
                            (
                                source.key.removeprefix("service:")
                                if source.key.startswith("service:")
                                else source.key.removeprefix("price:").rsplit(":", 1)[0]
                            )
                            for source in d.sources
                            if source.key.startswith("service:") or source.key.startswith("price:")
                        )
                    ),
                    active=is_active,
                    archived=is_archived,
                    submitted=is_submitted,
                )
            )
        return items

    async def add_ad(
        self,
        headline: str,
        text: str,
        now: datetime,
        rules_version: str = "wizard-1",
        *,
        active: bool = True,
        client_id: str | None = None,
    ) -> WizardAdItem:
        statement = (
            select(CreativeSetRow)
            .where(
                CreativeSetRow.business_id == self._business_id,
                CreativeSetRow.reason == "wizard",
            )
            .order_by(CreativeSetRow.id.desc())
            .limit(1)
            .with_for_update()
        )
        async with self._sessions.begin() as session:
            # Строки набора ещё может не быть: блокируем бизнес до поиска и создания.
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        WIZARD_AD_LOCK, func.hashtext(str(self._business_id))
                    )
                )
            )
            row = await session.scalar(statement)
            if row is None:
                row = CreativeSetRow(
                    business_id=self._business_id,
                    status=SetStatus.READY.value,
                    rules_version=rules_version,
                    reason="wizard",
                    guest_key=None,
                    token_hash=None,
                    profile={},
                    drafts=[],
                    kept=[],
                    created_at=func.now(),
                )
                session.add(row)
                await session.flush()
            drafts = [dict(d) for d in (row.drafts or [])]
            kept = list(row.kept or [])
            if client_id is not None:
                for index, draft in enumerate(drafts):
                    if draft.get("client_id") == client_id:
                        archived = bool(draft.get("archived", False))
                        return WizardAdItem(
                            id=f"w{index}",
                            index=index,
                            headline=str(draft["headline"]),
                            text=str(draft["text"]),
                            facts=tuple(s.get("label", "") for s in draft.get("sources", [])),
                            active=index in kept and not archived,
                            archived=archived,
                        )
            new_draft = {
                "headline": headline,
                "text": text,
                "sources": [{"label": "Ваш текст", "quote": ""}],
                "archived": False,
                "off": not active,
                "client_id": client_id,
            }
            drafts.append(new_draft)
            idx = len(drafts) - 1
            if active:
                kept.append(idx)
            row.drafts = drafts
            row.kept = kept
            flag_modified(row, "drafts")
            flag_modified(row, "kept")
            return WizardAdItem(
                id=f"w{idx}",
                index=idx,
                headline=headline,
                text=text,
                facts=("Ваш текст",),
                active=active,
                archived=False,
            )

    async def update_ad(self, index: int, headline: str, text: str) -> WizardAdItem | None:
        statement = (
            select(CreativeSetRow)
            .where(
                CreativeSetRow.business_id == self._business_id,
                CreativeSetRow.reason == "wizard",
            )
            .order_by(CreativeSetRow.id.desc())
            .limit(1)
            .with_for_update()
        )
        async with self._sessions.begin() as session:
            row = await session.scalar(statement)
            if row is None or not row.drafts or not (0 <= index < len(row.drafts)):
                return None
            drafts = [dict(d) for d in row.drafts]
            drafts[index]["headline"] = headline
            drafts[index]["text"] = text
            row.drafts = drafts
            flag_modified(row, "drafts")
            kept_set = set(row.kept or [])
            is_archived = bool(drafts[index].get("archived", False))
            return WizardAdItem(
                id=f"w{index}",
                index=index,
                headline=headline,
                text=text,
                facts=tuple(
                    s.get("label", "")
                    for s in drafts[index].get("sources", [{"label": "Ваш текст"}])
                ),
                active=(index in kept_set) and not is_archived,
                archived=is_archived,
            )

    async def set_active(self, index: int, active: bool) -> bool:
        statement = (
            select(CreativeSetRow)
            .where(
                CreativeSetRow.business_id == self._business_id,
                CreativeSetRow.reason == "wizard",
            )
            .order_by(CreativeSetRow.id.desc())
            .limit(1)
            .with_for_update()
        )
        async with self._sessions.begin() as session:
            row = await session.scalar(statement)
            if row is None or not row.drafts or not (0 <= index < len(row.drafts)):
                return False
            kept = list(row.kept or [])
            drafts = [dict(d) for d in row.drafts]
            if active:
                if index not in kept:
                    kept.append(index)
                drafts[index]["off"] = False
                if drafts[index].get("archived"):
                    drafts[index]["archived"] = False
            else:
                if index in kept:
                    kept.remove(index)
                drafts[index]["off"] = True
            row.drafts = drafts
            row.kept = kept
            flag_modified(row, "drafts")
            flag_modified(row, "kept")
            return True

    async def set_archived(self, index: int, archived: bool) -> bool:
        statement = (
            select(CreativeSetRow)
            .where(
                CreativeSetRow.business_id == self._business_id,
                CreativeSetRow.reason == "wizard",
            )
            .order_by(CreativeSetRow.id.desc())
            .limit(1)
            .with_for_update()
        )
        async with self._sessions.begin() as session:
            row = await session.scalar(statement)
            if row is None or not row.drafts or not (0 <= index < len(row.drafts)):
                return False
            drafts = [dict(d) for d in row.drafts]
            drafts[index]["archived"] = archived
            if not archived:
                drafts[index]["off"] = False
            row.drafts = drafts
            flag_modified(row, "drafts")
            kept = list(row.kept or [])
            if archived and index in kept:
                kept.remove(index)
            elif not archived and index not in kept:
                kept.append(index)
            row.kept = kept
            flag_modified(row, "kept")
            return True
