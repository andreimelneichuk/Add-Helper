"""Картинки одного бизнеса в PostgreSQL. Каждый запрос ограничен business_id.

Решение: байты лежат в базе (`bytea`), а не на диске. Своих фото не больше 5, сцен не больше 3
в сутки, после обработки каждая около 0,3–1 МБ; зато картинки попадают в резервную копию
и восстановление вместе с объявлениями, а второй том в развёртывании не нужен.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.creatives.adapters.sql import ACTIVE_IMAGE as ACTIVE
from adhelper.creatives.adapters.sql import CreativeImageRow, CreativeRow, PlatformImageRow
from adhelper.creatives.domain.images import ImageSource, NewImage, PreparedImage, StoredImage
from adhelper.creatives.domain.lifecycle import CreativeState
from adhelper.platforms.domain.ports import AdImage

ARCHIVED = "archived"
# Объявления, у которых картинка уже стоит или скоро встанет: по ним чередуем свои фото.
USING_STATES = (
    CreativeState.LAUNCH_PENDING.value,
    CreativeState.MODERATION.value,
    CreativeState.RUNNING.value,
)


def _stored(row: CreativeImageRow) -> StoredImage:
    return StoredImage(
        id=row.id,
        sha256=row.sha256,
        source=ImageSource(row.source),
        width=row.width,
        height=row.height,
        account=row.account,
        campaign_id=row.campaign_id,
        model=row.model,
        created_at=row.created_at,
    )


class SqlImages:
    """Свои фото, сгенерированные сцены и номера картинок на площадке."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def active(self, source: ImageSource | None = None) -> Sequence[StoredImage]:
        statement = select(CreativeImageRow).where(
            CreativeImageRow.business_id == self._business_id,
            CreativeImageRow.state == ACTIVE,
        )
        if source is not None:
            statement = statement.where(CreativeImageRow.source == source.value)
        async with self._sessions() as session:
            rows = await session.scalars(statement.order_by(CreativeImageRow.id))
            return [_stored(row) for row in rows]

    async def add(self, image: NewImage, now: datetime) -> int:
        """Та же картинка второй раз не хранится: возвращаем её номер и снова делаем активной."""
        prepared: PreparedImage = image.prepared
        values = {
            "business_id": self._business_id,
            "sha256": prepared.sha256,
            "source": image.source.value,
            "account": image.account,
            "campaign_id": image.campaign_id,
            "width": prepared.facts.width,
            "height": prepared.facts.height,
            "data": prepared.data,
            "prompt": image.prompt,
            "prompt_source": image.prompt_source,
            "model": image.model,
            "rules_version": image.rules_version,
            "cost_micro_usd": image.cost_micro_usd,
            "consent": image.consent,
            "state": ACTIVE,
            "created_at": now,
        }
        statement = (
            insert(CreativeImageRow)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_creative_images_sha256",
                set_={"state": ACTIVE, "archived_at": None, "consent": image.consent},
            )
            .returning(CreativeImageRow.id)
        )
        async with self._sessions.begin() as session:
            return int((await session.execute(statement)).scalar_one())

    async def archive(self, image_id: int, now: datetime) -> bool:
        """Картинка убирается из подбора; объявления, где она уже стоит, работают дальше."""
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(CreativeImageRow)
                .where(
                    CreativeImageRow.business_id == self._business_id,
                    CreativeImageRow.id == image_id,
                    CreativeImageRow.state == ACTIVE,
                )
                .values(state=ARCHIVED, archived_at=now)
                .returning(CreativeImageRow.id)
            )
            return result.first() is not None

    async def content(self, image_id: int) -> bytes | None:
        async with self._sessions() as session:
            found = await session.scalar(
                select(CreativeImageRow.data).where(
                    CreativeImageRow.business_id == self._business_id,
                    CreativeImageRow.id == image_id,
                )
            )
            return bytes(found) if found is not None else None

    async def generated_since(self, since: datetime) -> int:
        async with self._sessions() as session:
            found = await session.scalar(
                select(func.count())
                .select_from(CreativeImageRow)
                .where(
                    CreativeImageRow.business_id == self._business_id,
                    CreativeImageRow.source == ImageSource.GENERATED.value,
                    CreativeImageRow.created_at >= since,
                )
            )
            return int(found or 0)

    async def direction_image(self, account: str, campaign_id: str) -> int | None:
        async with self._sessions() as session:
            found = await session.scalar(
                select(CreativeImageRow.id)
                .where(
                    CreativeImageRow.business_id == self._business_id,
                    CreativeImageRow.source == ImageSource.GENERATED.value,
                    CreativeImageRow.state == ACTIVE,
                    CreativeImageRow.account == account,
                    CreativeImageRow.campaign_id == campaign_id,
                )
                .order_by(CreativeImageRow.id.desc())
                .limit(1)
            )
            return int(found) if found is not None else None

    async def used(self) -> Sequence[int]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(CreativeRow.image_id).where(
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.image_id.is_not(None),
                    CreativeRow.state.in_(USING_STATES),
                )
            )
            return [int(row) for row in rows if row is not None]

    async def attach(self, creative_id: int, image_id: int) -> bool:
        """Картинка ставится только объявлению, которое ещё не ушло на площадку."""
        async with self._sessions.begin() as session:
            owned = await session.scalar(
                select(CreativeImageRow.id).where(
                    CreativeImageRow.business_id == self._business_id,
                    CreativeImageRow.id == image_id,
                )
            )
            if owned is None:
                return False
            result = await session.execute(
                update(CreativeRow)
                .where(
                    CreativeRow.business_id == self._business_id,
                    CreativeRow.id == creative_id,
                    CreativeRow.ad_id.is_(None),
                    CreativeRow.image_id.is_(None),
                )
                .values(image_id=image_id)
                .returning(CreativeRow.id)
            )
            return result.first() is not None

    # --- ImageVault: для исполнителя журнала ---

    async def image_by_sha(self, sha256: str) -> AdImage | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(CreativeImageRow).where(
                    CreativeImageRow.business_id == self._business_id,
                    CreativeImageRow.sha256 == sha256,
                )
            )
            if row is None:
                return None
            # Имя в библиотеке Директа: только наш номер, без данных бизнеса.
            return AdImage(row.sha256, bytes(row.data), f"ahc-image-{row.id}")

    async def platform_hash(self, account: str, sha256: str) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(PlatformImageRow.image_hash).where(
                    PlatformImageRow.business_id == self._business_id,
                    PlatformImageRow.account == account,
                    PlatformImageRow.sha256 == sha256,
                )
            )

    async def remember_hash(self, account: str, sha256: str, image_hash: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                insert(PlatformImageRow)
                .values(
                    business_id=self._business_id,
                    account=account,
                    sha256=sha256,
                    image_hash=image_hash,
                    created_at=func.now(),
                )
                .on_conflict_do_update(
                    constraint="pk_platform_images", set_={"image_hash": image_hash}
                )
            )

    async def sha_of(self, account: str, image_hash: str) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(PlatformImageRow.sha256).where(
                    PlatformImageRow.business_id == self._business_id,
                    PlatformImageRow.account == account,
                    PlatformImageRow.image_hash == image_hash,
                )
            )
