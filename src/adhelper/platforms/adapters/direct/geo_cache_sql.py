"""Persistent, shared cache for the Direct GeoRegions reference tree."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from adhelper.db import Base


class DirectGeoRegionsRow(Base):
    __tablename__ = "direct_geo_regions"

    cache_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    regions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SqlGeoRegionCache:
    """A single global cache: Direct's region directory is not account-specific."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, max_age: timedelta = timedelta(days=1)) -> list[dict[str, Any]] | None:
        async with self._sessions() as session:
            row = await session.get(DirectGeoRegionsRow, 1)
        if row is None:
            return None
        updated = row.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if datetime.now(UTC) - updated > max_age:
            return None
        return row.regions

    async def put(self, regions: Sequence[Mapping[str, Any]]) -> None:
        rows = [dict(item) for item in regions]
        statement = (
            insert(DirectGeoRegionsRow)
            .values(cache_id=1, regions=rows, updated_at=func.now())
            .on_conflict_do_update(
                index_elements=["cache_id"],
                set_={"regions": rows, "updated_at": func.now()},
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)
