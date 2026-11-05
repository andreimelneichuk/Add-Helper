from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Uuid, column, func, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

GOALS = table(
    "lead_goals",
    column("business_id", Uuid),
    column("goal_micros", BigInteger),
    column("updated_at", DateTime(timezone=True)),
)


class SqlLeadGoal:
    """Желаемая цена обращения одного бизнеса. Каждый запрос ограничен business_id."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def get(self) -> int | None:
        statement = select(GOALS.c.goal_micros).where(GOALS.c.business_id == self._business_id)
        async with self._sessions() as session:
            return await session.scalar(statement)

    async def set(self, goal_micros: int) -> None:
        statement = insert(GOALS).values(business_id=self._business_id, goal_micros=goal_micros)
        statement = statement.on_conflict_do_update(
            index_elements=[GOALS.c.business_id],
            set_={"goal_micros": goal_micros, "updated_at": func.now()},
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)
