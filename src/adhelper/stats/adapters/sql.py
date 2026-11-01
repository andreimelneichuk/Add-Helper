"""Чтение для экранов поверх таблиц других модулей. Только SELECT и только одного бизнеса."""

from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Text,
    Uuid,
    column,
    func,
    select,
    table,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.stats.domain.model import (
    AlertLine,
    CampaignLine,
    DayLine,
    HistoryLine,
    PeriodLine,
    Totals,
)

PERIODS = table(
    "budget_periods",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("account", Text),
    column("starts_at", DateTime(timezone=True)),
    column("ends_at", DateTime(timezone=True)),
    column("hard_limit_micros", BigInteger),
    column("reserve_bp", Integer),
    column("rules_version", Text),
    column("state", Text),
    column("stop_pending", Boolean),
    column("opening_balance_micros", BigInteger),
    column("opening_spent_micros", BigInteger),
    column("last_balance_micros", BigInteger),
    column("balance_checked_at", DateTime(timezone=True)),
)
SNAPSHOTS = table(
    "spend_snapshots",
    column("period_id", BigInteger),
    column("reported_micros", BigInteger),
    column("data_as_of", DateTime(timezone=True)),
)
ALERTS = table(
    "alerts",
    column("business_id", Uuid),
    column("kind", Text),
    column("details", JSONB),
    column("created_at", DateTime(timezone=True)),
    column("resolved_at", DateTime(timezone=True)),
)
CAMPAIGNS = table(
    "campaigns",
    column("business_id", Uuid),
    column("account", Text),
    column("campaign_id", Text),
    column("name", Text),
    column("state", Text),
    column("limit_micros", BigInteger),
)
STATS = table(
    "campaign_stats",
    column("business_id", Uuid),
    column("account", Text),
    column("campaign_id", Text),
    column("day", Date),
    column("impressions", BigInteger),
    column("clicks", BigInteger),
    column("cost_micros", BigInteger),
    column("conversions", BigInteger),
)
CHANGES = table(
    "changes",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("source", Text),
    column("kind", Text),
    column("target_ref", Text),
    column("status", Text),
    column("explanation", Text),
    column("before", JSONB),
    column("after", JSONB),
    column("created_at", DateTime(timezone=True)),
)
DECISIONS = table(
    "decisions",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("object_ref", Text),
    column("verdict", Text),
    column("action", Text),
    column("status", Text),
    column("inputs", JSONB),
    column("rules_version", Text),
    column("decided_at", DateTime(timezone=True)),
)
# Закрытый период показываем ещё сутки: владелец должен увидеть, чем он кончился.
SHOW_CLOSED_FOR = timedelta(days=1)


def _sums() -> list[object]:
    return [
        func.coalesce(func.sum(STATS.c.impressions), 0),
        func.coalesce(func.sum(STATS.c.clicks), 0),
        func.coalesce(func.sum(STATS.c.cost_micros), 0),
        func.coalesce(func.sum(STATS.c.conversions), 0),
    ]


def _totals(impressions: int, clicks: int, cost: int, conversions: int) -> Totals:
    return Totals(int(impressions), int(clicks), int(cost), int(conversions))


class SqlDashboard:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def periods(self, now: datetime) -> list[PeriodLine]:
        latest = (
            select(SNAPSHOTS.c.reported_micros, SNAPSHOTS.c.data_as_of)
            .where(SNAPSHOTS.c.period_id == PERIODS.c.id)
            .order_by(SNAPSHOTS.c.data_as_of.desc())
            .limit(1)
            .lateral()
        )
        statement = (
            select(PERIODS, latest.c.reported_micros, latest.c.data_as_of)
            .outerjoin(latest, true())
            .where(
                PERIODS.c.business_id == self._business_id,
                PERIODS.c.starts_at <= now,
                PERIODS.c.ends_at > now - SHOW_CLOSED_FOR,
            )
            .order_by(PERIODS.c.account, PERIODS.c.starts_at)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [
            PeriodLine(
                account=row["account"],
                rules_version=row["rules_version"],
                starts_at=row["starts_at"],
                ends_at=row["ends_at"],
                hard_limit=row["hard_limit_micros"],
                reserve_bp=row["reserve_bp"],
                state=row["state"],
                stop_pending=row["stop_pending"],
                spent=row["reported_micros"],
                data_as_of=row["data_as_of"],
                opening_balance=row["opening_balance_micros"],
                opening_spent=row["opening_spent_micros"],
                last_balance=row["last_balance_micros"],
                balance_checked_at=row["balance_checked_at"],
            )
            for row in rows
        ]

    async def alerts(self) -> list[AlertLine]:
        statement = (
            select(ALERTS.c.kind, ALERTS.c.details, ALERTS.c.created_at)
            .where(ALERTS.c.business_id == self._business_id, ALERTS.c.resolved_at.is_(None))
            .order_by(ALERTS.c.created_at.desc())
        )
        async with self._sessions() as session:
            return [AlertLine(*row) for row in await session.execute(statement)]

    async def campaigns(self, since: date, until: date) -> list[CampaignLine]:
        sums = (
            select(STATS.c.account, STATS.c.campaign_id, *_sums())
            .where(
                STATS.c.business_id == self._business_id,
                STATS.c.day >= since,
                STATS.c.day <= until,
            )
            .group_by(STATS.c.account, STATS.c.campaign_id)
            .subquery()
        )
        statement = (
            select(
                CAMPAIGNS.c.account,
                CAMPAIGNS.c.campaign_id,
                CAMPAIGNS.c.name,
                CAMPAIGNS.c.state,
                CAMPAIGNS.c.limit_micros,
                *[func.coalesce(c, 0) for c in list(sums.c)[2:]],
            )
            .outerjoin(
                sums,
                (sums.c.account == CAMPAIGNS.c.account)
                & (sums.c.campaign_id == CAMPAIGNS.c.campaign_id),
            )
            .where(CAMPAIGNS.c.business_id == self._business_id)
            .order_by(CAMPAIGNS.c.name)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            CampaignLine(
                account=row[0],
                campaign_id=row[1],
                name=row[2],
                state=row[3],
                limit=row[4],
                totals=_totals(row[5], row[6], row[7], row[8]),
            )
            for row in rows
        ]

    async def days(self, since: date, until: date) -> list[DayLine]:
        statement = (
            select(STATS.c.day, *_sums())
            .where(
                STATS.c.business_id == self._business_id,
                STATS.c.day >= since,
                STATS.c.day <= until,
            )
            .group_by(STATS.c.day)
            .order_by(STATS.c.day)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [DayLine(row[0], _totals(*row[1:])) for row in rows]

    async def history(self, limit: int) -> list[HistoryLine]:
        changes = (
            select(CHANGES)
            .where(CHANGES.c.business_id == self._business_id)
            .order_by(CHANGES.c.created_at.desc(), CHANGES.c.id.desc())
            .limit(limit)
        )
        decisions = (
            select(DECISIONS)
            .where(DECISIONS.c.business_id == self._business_id)
            .order_by(DECISIONS.c.decided_at.desc(), DECISIONS.c.id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            change_rows = (await session.execute(changes)).mappings().all()
            decision_rows = (await session.execute(decisions)).mappings().all()
        lines = [
            HistoryLine(
                ref=f"change-{row['id']}",
                at=row["created_at"],
                source=row["source"],
                kind=row["kind"],
                target=row["target_ref"],
                status=row["status"],
                explanation=row["explanation"],
                before=row["before"],
                after=row["after"],
                rules_version=None,
            )
            for row in change_rows
        ] + [
            HistoryLine(
                ref=f"decision-{row['id']}",
                at=row["decided_at"],
                source="autopilot",
                kind=row["verdict"],
                target=row["object_ref"],
                status=row["status"],
                explanation=row["action"] or "",
                before=row["inputs"],
                after=None,
                rules_version=row["rules_version"],
            )
            for row in decision_rows
        ]
        return sorted(lines, key=lambda line: line.at, reverse=True)[:limit]
