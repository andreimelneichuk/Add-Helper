"""Отправка объявлений и итог проверки Яндексом для одного бизнеса.

Витрина `ads` здесь меняется вместе со строками `creatives` одной транзакцией: экран и
хранитель мест видят одно и то же. Площадку меняет только журнал, этот слой её не трогает.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, column, or_, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.creatives.adapters.sql import (
    CAMPAIGNS,
    CreativeRow,
    CreativeSetRow,
    _event,
    _set_state,
)
from adhelper.creatives.domain.lifecycle import CreativeState, LiveAd
from adhelper.creatives.domain.model import AdDraft, SetStatus
from adhelper.creatives.domain.moderation import Checking, ModerationPlan, ModerationRules
from adhelper.creatives.domain.ports import Outgoing

ADS = table(
    "ads",
    column("business_id"),
    column("account"),
    column("ad_id"),
    column("campaign_id"),
    column("title"),
    column("text"),
    column("href"),
    column("state"),
)
# Причина набора ремонта и объявлений из него.
REPAIR_REASON = "moderation_rejected"
NEEDS_OWNER = "needs_owner"
REPAIRING = "repairing"
SUPERSEDED = "superseded"


class SqlModeration:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def accounts(self) -> Sequence[str]:
        statement = (
            select(ADS.c.account)
            .where(ADS.c.business_id == self._business_id)
            .distinct()
            .order_by(ADS.c.account)
        )
        async with self._sessions() as session:
            return [str(account) for account in await session.scalars(statement)]

    async def outgoing(self) -> Sequence[Outgoing]:
        """Занявшие место объявления, у направления которых есть кампания на площадке."""
        statement = (
            select(CreativeRow)
            .join(
                CAMPAIGNS,
                and_(
                    CAMPAIGNS.c.business_id == CreativeRow.business_id,
                    CAMPAIGNS.c.account == CreativeRow.account,
                    CAMPAIGNS.c.campaign_id == CreativeRow.campaign_id,
                ),
            )
            .where(
                CreativeRow.business_id == self._business_id,
                CreativeRow.state == CreativeState.LAUNCH_PENDING.value,
                CreativeRow.ad_id.is_(None),
                CAMPAIGNS.c.state != "archived",
            )
            .order_by(CreativeRow.id)
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(statement))
            hrefs = {
                (str(a), str(c)): str(h)
                for a, c, h in await session.execute(
                    select(ADS.c.account, ADS.c.campaign_id, ADS.c.href).where(
                        ADS.c.business_id == self._business_id, ADS.c.href != ""
                    )
                )
            }
        return [
            Outgoing(
                row.id,
                row.account,
                row.campaign_id,
                AdDraft(row.headline, row.text),
                hrefs.get((row.account, row.campaign_id), ""),
            )
            for row in rows
        ]

    async def sent(self, creative_id: int, ad_id: str, href: str, now: datetime) -> bool:
        """Журнал прочитал созданное: объявление ждёт проверки и видно в кабинете."""
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(CreativeRow)
                .where(
                    CreativeRow.id == creative_id,
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.state == CreativeState.LAUNCH_PENDING.value,
                )
                .with_for_update()
            )
            if row is None:
                return False
            row.ad_id = ad_id
            _set_state(row, CreativeState.MODERATION, "sent", now)
            exists = await session.scalar(
                select(ADS.c.ad_id).where(
                    ADS.c.business_id == self._business_id,
                    ADS.c.account == row.account,
                    ADS.c.ad_id == ad_id,
                )
            )
            if exists is None:
                await session.execute(
                    ADS.insert().values(
                        business_id=self._business_id,
                        account=row.account,
                        ad_id=ad_id,
                        campaign_id=row.campaign_id,
                        title=row.headline,
                        text=row.text,
                        href=href,
                        state="moderation",
                    )
                )
            return True

    async def checking(self) -> Sequence[Checking]:
        statement = (
            select(
                ADS.c.account,
                ADS.c.campaign_id,
                ADS.c.ad_id,
                ADS.c.title,
                ADS.c.text,
                CreativeRow.id,
                CreativeRow.replaces,
                CreativeRow.repairs,
            )
            .outerjoin(
                CreativeRow,
                and_(
                    CreativeRow.business_id == ADS.c.business_id,
                    CreativeRow.account == ADS.c.account,
                    CreativeRow.ad_id == ADS.c.ad_id,
                ),
            )
            .where(ADS.c.business_id == self._business_id, ADS.c.state == "moderation")
            .order_by(ADS.c.account, ADS.c.ad_id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            Checking(
                str(r[0]), str(r[1]), str(r[2]), AdDraft(r[3], r[4]), r[5], r[6], int(r[7] or 0)
            )
            for r in rows
        ]

    async def last_rejections(self) -> Mapping[tuple[str, str], str]:
        statement = (
            select(CreativeRow.account, CreativeRow.campaign_id, CreativeRow.rejection)
            .where(
                CreativeRow.business_id == self._business_id,
                CreativeRow.rejection.is_not(None),
            )
            .order_by(CreativeRow.updated_at, CreativeRow.id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        # Последняя по времени перекрывает прежние.
        return {(str(a), str(c)): str(code) for a, c, code in rows}

    async def apply(
        self,
        plan: ModerationPlan,
        rules: ModerationRules,
        creative_rules: str,
        rework_rules: str,
        now: datetime,
    ) -> Sequence[LiveAd]:
        """Итоги проверки одной транзакцией. Возвращает объявления, которые теперь заменены
        одобренными и должны остановиться через журнал.
        """
        to_pause: list[LiveAd] = []
        async with self._sessions.begin() as session:
            for ad in plan.accepted:
                if not await self._move_ad(session, ad, "moderation", "on"):
                    continue
                if ad.creative_id is not None:
                    row = await self._creative(session, ad.creative_id)
                    if row is not None:
                        _set_state(row, CreativeState.RUNNING, "accepted", now)
                if ad.replaces is not None:
                    old = await self._supersede(session, ad.account, ad.replaces, now)
                    if old is not None:
                        to_pause.append(old)
            for rejection in plan.rejected:
                ad = rejection.ad
                repairing = rejection.repair is not None
                # Ремонт займёт место сам; без ремонта отклонённое держит место до решения
                # владельца. Отклонённая замена места не держала никогда.
                keeps_place = not repairing and ad.replaces is None
                state = "rejected" if keeps_place else "archived"
                if not await self._move_ad(session, ad, "moderation", state):
                    continue
                row = await self._own_row(session, ad, now)
                row.rejection = rejection.kind.code
                _set_state(
                    row, CreativeState.REJECTED, REPAIRING if repairing else NEEDS_OWNER, now
                )
                if rejection.repair is None:
                    continue
                session.add(
                    CreativeSetRow(
                        business_id=self._business_id,
                        status=SetStatus.QUEUED.value,
                        rules_version=creative_rules,
                        created_at=now,
                        account=ad.account,
                        campaign_id=ad.campaign_id,
                        reason=REPAIR_REASON,
                        wanted=1,
                        rework={
                            "ad_id": ad.ad_id,
                            "headline": ad.draft.headline,
                            "text": ad.draft.text,
                            "wish": rejection.kind.wish,
                            "rules": rework_rules,
                            "auto": True,
                            "repair": rejection.repair,
                            "replaces": ad.replaces,
                            "moderation_rules": rules.version,
                        },
                    )
                )
        return to_pause

    async def replaced(self, account: str, ad_id: str, now: datetime) -> None:
        """Журнал остановил заменённое объявление: витрина и строка объявления это отражают."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(ADS)
                .where(
                    ADS.c.business_id == self._business_id,
                    ADS.c.account == account,
                    ADS.c.ad_id == ad_id,
                )
                .values(state="suspended")
            )
            row = await session.scalar(
                select(CreativeRow)
                .where(
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.account == account,
                    CreativeRow.ad_id == ad_id,
                    CreativeRow.state == CreativeState.RUNNING.value,
                )
                .with_for_update()
            )
            if row is not None:
                _set_state(row, CreativeState.STOPPED, "replaced", now)

    async def waiting_owner(self) -> Sequence[Mapping[str, str]]:
        """Отклонённые объявления, о которых решает владелец: ремонт исчерпан или не поможет."""
        statement = (
            select(
                CreativeRow.account, CreativeRow.ad_id, CreativeRow.headline, CreativeRow.rejection
            )
            .outerjoin(
                ADS,
                and_(
                    ADS.c.business_id == CreativeRow.business_id,
                    ADS.c.account == CreativeRow.account,
                    ADS.c.ad_id == CreativeRow.ad_id,
                ),
            )
            .where(
                CreativeRow.business_id == self._business_id,
                CreativeRow.state == CreativeState.REJECTED.value,
                CreativeRow.state_reason == NEEDS_OWNER,
                # Своё место: пока отклонённое в витрине. Замена: пока её не сменила другая.
                or_(CreativeRow.replaces.is_not(None), ADS.c.state == "rejected"),
            )
            .order_by(CreativeRow.id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            {"account": str(a), "ad_id": str(i), "headline": str(h), "reason": str(r)}
            for a, i, h, r in rows
        ]

    async def _creative(self, session: AsyncSession, creative_id: int) -> CreativeRow | None:
        return await session.scalar(
            select(CreativeRow)
            .where(CreativeRow.id == creative_id, CreativeRow.business_id == self._business_id)
            .with_for_update()
        )

    async def _move_ad(self, session: AsyncSession, ad: Checking, was: str, state: str) -> bool:
        """Переход витрины только из ожидаемого состояния: повтор цикла ничего не задвоит."""
        result = await session.execute(
            update(ADS)
            .where(
                ADS.c.business_id == self._business_id,
                ADS.c.account == ad.account,
                ADS.c.ad_id == ad.ad_id,
                ADS.c.state == was,
            )
            .values(state=state)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def _own_row(self, session: AsyncSession, ad: Checking, now: datetime) -> CreativeRow:
        """Строка объявления; у объявлений запуска из мастера её создаём при первом отказе."""
        if ad.creative_id is not None:
            row = await self._creative(session, ad.creative_id)
            if row is not None:
                return row
        row = CreativeRow(
            business_id=self._business_id,
            account=ad.account,
            campaign_id=ad.campaign_id,
            headline=ad.draft.headline,
            text=ad.draft.text,
            source="launch",
            auto_ok=False,
            reason="launch",
            facts=[],
            sources=[],
            state=CreativeState.MODERATION.value,
            state_reason="sent",
            history=[_event(now, CreativeState.MODERATION, "sent")],
            replaces=None,
            ad_id=ad.ad_id,
            repairs=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        return row

    async def _supersede(
        self, session: AsyncSession, account: str, target: str, now: datetime
    ) -> LiveAd | None:
        """Одобрена замена `target`: отклонённое уходит в архив, работающее — на остановку."""
        await session.execute(
            update(CreativeRow)
            .where(
                CreativeRow.business_id == self._business_id,
                CreativeRow.account == account,
                CreativeRow.state == CreativeState.REJECTED.value,
                CreativeRow.state_reason == NEEDS_OWNER,
                or_(CreativeRow.ad_id == target, CreativeRow.replaces == target),
            )
            .values(state_reason=SUPERSEDED, updated_at=now)
        )
        old = (
            await session.execute(
                select(ADS.c.campaign_id, ADS.c.title, ADS.c.text, ADS.c.state).where(
                    ADS.c.business_id == self._business_id,
                    ADS.c.account == account,
                    ADS.c.ad_id == target,
                )
            )
        ).first()
        if old is None:
            return None
        if old.state == "rejected":
            await session.execute(
                update(ADS)
                .where(
                    ADS.c.business_id == self._business_id,
                    ADS.c.account == account,
                    ADS.c.ad_id == target,
                )
                .values(state="archived")
            )
            return None
        if old.state != "on":
            return None
        return LiveAd(account, str(old.campaign_id), target, AdDraft(old.title, old.text))
