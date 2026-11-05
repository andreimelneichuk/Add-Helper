"""Данные для хода теста одного бизнеса: контроль из витрины, профиль, итоги вариантов.

Каждый запрос ограничен business_id. Итоги вариантов берём из `ad_stats` (отчёт площадки
по объявлениям) с первых полных московских суток после старта: в день старта вариант
показывался не весь день, и контроль получил бы фору. `SqlAdStats` пишет строки отчёта.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import BigInteger, Date, DateTime, Text, Uuid, column, func, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.budget.domain.calendar import platform_date
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.domain.model import AdDraft, BusinessProfile
from adhelper.experiments.domain.model import ExperimentRecord, Observation, VariantStats
from adhelper.platforms.domain.ports import AdDay

ADS = table(
    "ads",
    column("business_id", Uuid),
    column("account", Text),
    column("ad_id", Text),
    column("title", Text),
    column("text", Text),
    column("href", Text),
)
AD_STATS = table(
    "ad_stats",
    column("business_id", Uuid),
    column("account", Text),
    column("ad_id", Text),
    column("day", Date),
    column("impressions", BigInteger),
    column("clicks", BigInteger),
    column("cost_micros", BigInteger),
    column("conversions", BigInteger),
    column("loaded_at", DateTime(timezone=True)),
)
ALERTS = table(
    "alerts",
    column("business_id", Uuid),
    column("kind", Text),
    column("resolved_at", DateTime(timezone=True)),
)
EXPERIMENTS = table(
    "experiments",
    column("business_id", Uuid),
    column("account", Text),
    column("status", Text),
)
OPEN = ("review", "running", "paused")
AD_PREFIX = "ad:"
# Тревога суточного цикла: данным нельзя верить, тест встаёт на паузу.
UNTRUSTED = "data_untrusted"


class SqlVariantContext:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def control(self, account: str, ad_id: str) -> tuple[AdDraft, str] | None:
        statement = select(ADS.c.title, ADS.c.text, ADS.c.href).where(
            ADS.c.business_id == self._business_id,
            ADS.c.account == account,
            ADS.c.ad_id == ad_id,
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).first()
        if row is None or not row.href:
            return None
        return AdDraft(str(row.title), str(row.text)), str(row.href)

    async def profile(self) -> BusinessProfile | None:
        found = await SqlProfiles(self._sessions, self._business_id).get()
        return found[0] if found else None


class SqlObservations:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def observe(self, record: ExperimentRecord) -> Observation:
        refs = [record.control_ref, record.variant_ref or ""]
        ids = [ref.removeprefix(AD_PREFIX) for ref in refs]
        since = platform_date(record.started_at) if record.started_at else None
        conditions = [
            AD_STATS.c.business_id == self._business_id,
            AD_STATS.c.account == record.account,
            AD_STATS.c.ad_id.in_(ids),
        ]
        if since is not None:
            conditions.append(AD_STATS.c.day > since)
        statement = (
            select(
                AD_STATS.c.ad_id,
                func.sum(AD_STATS.c.clicks),
                func.sum(AD_STATS.c.conversions),
                func.sum(AD_STATS.c.cost_micros),
            )
            .where(*conditions)
            .group_by(AD_STATS.c.ad_id)
        )
        untrusted = select(func.count()).where(
            ALERTS.c.business_id == self._business_id,
            ALERTS.c.kind == UNTRUSTED,
            ALERTS.c.resolved_at.is_(None),
        )
        async with self._sessions() as session:
            rows = {
                str(ad): VariantStats(int(clicks), int(leads), int(cost))
                for ad, clicks, leads, cost in (await session.execute(statement)).all()
            }
            open_alerts = await session.scalar(untrusted)
        quiet = VariantStats(0, 0, 0)
        control, variant = (rows.get(ad_id, quiet) for ad_id in ids)
        return Observation(control, variant, control.spend + variant.spend, not open_alerts)


class SqlAdStats:
    """Строки отчёта площадки по объявлениям: повтор за те же сутки заменяет, а не складывает."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def save(self, account: str, rows: Sequence[AdDay]) -> int:
        if not rows:
            return 0
        statement = insert(AD_STATS).values(
            [
                {
                    "business_id": self._business_id,
                    "account": account,
                    "ad_id": row.ad_id,
                    "day": row.day,
                    "impressions": row.impressions,
                    "clicks": row.clicks,
                    "cost_micros": row.cost,
                    "conversions": row.conversions,
                }
                for row in rows
            ]
        )
        statement = statement.on_conflict_do_update(
            index_elements=["business_id", "account", "ad_id", "day"],
            set_={
                "impressions": statement.excluded.impressions,
                "clicks": statement.excluded.clicks,
                "cost_micros": statement.excluded.cost_micros,
                "conversions": statement.excluded.conversions,
                "loaded_at": func.now(),
            },
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)
        return len(rows)


async def experiment_accounts(
    sessions: async_sessionmaker[AsyncSession],
) -> list[tuple[UUID, str]]:
    """Кабинеты с открытыми тестами: остальным проходу делать нечего."""
    statement = (
        select(EXPERIMENTS.c.business_id, EXPERIMENTS.c.account)
        .where(EXPERIMENTS.c.status.in_(OPEN))
        .distinct()
        .order_by(EXPERIMENTS.c.business_id, EXPERIMENTS.c.account)
    )
    async with sessions() as session:
        return [(UUID(str(b)), str(a)) for b, a in (await session.execute(statement)).all()]
