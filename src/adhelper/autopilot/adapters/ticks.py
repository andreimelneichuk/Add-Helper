"""Журнал часовых тиков автопилота: считаем, сколько часов воркер не просыпался."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

HOUR = timedelta(hours=1)


def slot_of(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


class SqlTicks:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, now: datetime, accounts: int) -> None:
        """Тик часа записывается один раз; повтор в тот же час обновляет число кабинетов."""
        async with self._sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO autopilot_ticks (slot, ran_at, accounts) VALUES (:slot, :now, :n)"
                    " ON CONFLICT (slot) DO UPDATE SET ran_at = EXCLUDED.ran_at,"
                    " accounts = EXCLUDED.accounts"
                ),
                {"slot": slot_of(now), "now": now, "n": accounts},
            )

    async def missed(self, start: datetime, end: datetime) -> tuple[int, int]:
        """(ждали, пропущено) за [start, end): ждём с первого записанного тика, не раньше start."""
        first_slot = slot_of(start) if slot_of(start) >= start else slot_of(start) + HOUR
        last_slot = slot_of(end) - HOUR
        async with self._sessions() as session:
            first = await session.scalar(text("SELECT min(slot) FROM autopilot_ticks"))
            if first is None or first > last_slot:
                return 0, 0
            first_slot = max(first_slot, first)
            have = await session.scalar(
                text("SELECT count(*) FROM autopilot_ticks WHERE slot >= :a AND slot <= :b"),
                {"a": first_slot, "b": last_slot},
            )
        expected = int((last_slot - first_slot) / HOUR) + 1
        return expected, max(expected - int(have or 0), 0)
