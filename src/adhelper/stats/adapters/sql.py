"""Чтение для экранов поверх таблиц других модулей. Только SELECT и только одного бизнеса."""

from datetime import date, datetime, timedelta
from typing import Any
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
    literal,
    select,
    table,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import ColumnElement, TableClause

from adhelper.stats.domain.model import (
    AdLine,
    AlertLine,
    CampaignLine,
    ClientAnswer,
    DayLine,
    HistoryLine,
    PeriodLine,
    Scope,
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
CLIENTS = table(
    "client_answers",
    column("business_id", Uuid),
    column("since", Date),
    column("until", Date),
    column("leads", Integer),
    column("clients", Integer),
    column("answered_at", DateTime(timezone=True)),
)
CAMPAIGNS = table(
    "campaigns",
    column("business_id", Uuid),
    column("account", Text),
    column("campaign_id", Text),
    column("name", Text),
    column("state", Text),
    column("limit_micros", BigInteger),
    column("targeting", JSONB),
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
ADS = table(
    "ads",
    column("business_id", Uuid),
    column("account", Text),
    column("ad_id", Text),
    column("campaign_id", Text),
    column("title", Text),
    column("text", Text),
    column("href", Text),
    column("state", Text),
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


def _sums(source: TableClause = STATS) -> list[ColumnElement[int]]:
    return [
        func.coalesce(func.sum(source.c.impressions), 0),
        func.coalesce(func.sum(source.c.clicks), 0),
        func.coalesce(func.sum(source.c.cost_micros), 0),
        func.coalesce(func.sum(source.c.conversions), 0),
    ]


def _totals(impressions: int, clicks: int, cost: int, conversions: int) -> Totals:
    return Totals(int(impressions), int(clicks), int(cost), int(conversions))


class SqlDashboard:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def leads(self, since: date, until: date) -> int:
        """Обращения по статистике площадки за окно дат включительно."""
        statement = select(func.coalesce(func.sum(STATS.c.conversions), 0)).where(
            STATS.c.business_id == self._business_id,
            STATS.c.day >= since,
            STATS.c.day <= until,
        )
        async with self._sessions() as session:
            return int(await session.scalar(statement) or 0)

    async def save_clients(self, answer: ClientAnswer) -> None:
        values = {
            "business_id": self._business_id,
            "since": answer.since,
            "until": answer.until,
            "leads": answer.leads,
            "clients": answer.clients,
        }
        statement = (
            pg_insert(CLIENTS)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["business_id", "since", "until"],
                set_={"leads": answer.leads, "clients": answer.clients, "answered_at": func.now()},
            )
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    async def latest_clients(self) -> ClientAnswer | None:
        statement = (
            select(CLIENTS.c.since, CLIENTS.c.until, CLIENTS.c.leads, CLIENTS.c.clients)
            .where(CLIENTS.c.business_id == self._business_id)
            .order_by(CLIENTS.c.until.desc(), CLIENTS.c.answered_at.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).one_or_none()
        return ClientAnswer(*row) if row else None

    async def note_ad_state(self, account: str, ad_id: str, state: str) -> None:
        """Результат, прочитанный журналом. Полную сверку с площадкой делает синхронизация (T6)."""
        await self._note(ADS, account, ADS.c.ad_id == ad_id, state)

    async def note_campaign_state(self, account: str, campaign_id: str, state: str) -> None:
        await self._note(CAMPAIGNS, account, CAMPAIGNS.c.campaign_id == campaign_id, state)

    async def note_targeting(self, account: str, campaign_id: str, patch: dict[str, Any]) -> None:
        """После применённого изменения показа: иначе экран до синхронизации покажет старое."""
        current = func.coalesce(CAMPAIGNS.c.targeting, literal({}, JSONB))
        statement = (
            CAMPAIGNS.update()
            .where(
                CAMPAIGNS.c.business_id == self._business_id,
                CAMPAIGNS.c.account == account,
                CAMPAIGNS.c.campaign_id == campaign_id,
            )
            .values(targeting=current.op("||")(literal(patch, JSONB)))
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    async def note_created_campaign(
        self,
        account: str,
        campaign_id: str,
        name: str,
        limit: int,
        ads: list[tuple[str, str, str, str]],
    ) -> None:
        """Кампания запуска, прочитанная журналом: до синхронизации она уже видна в кабинете.

        Новое в Директе сначала проходит проверку, поэтому состояние «moderation».
        Объявления: номер, заголовок, текст, ссылка. Повтор ничего не дублирует.
        """
        campaign = (
            pg_insert(CAMPAIGNS)
            .values(
                business_id=self._business_id,
                account=account,
                campaign_id=campaign_id,
                name=name,
                state="moderation",
                limit_micros=limit,
            )
            .on_conflict_do_nothing(index_elements=["business_id", "account", "campaign_id"])
        )
        async with self._sessions() as session, session.begin():
            await session.execute(campaign)
            for ad_id, title, body, href in ads:
                await session.execute(
                    pg_insert(ADS)
                    .values(
                        business_id=self._business_id,
                        account=account,
                        ad_id=ad_id,
                        campaign_id=campaign_id,
                        title=title,
                        text=body,
                        href=href,
                        state="moderation",
                    )
                    .on_conflict_do_nothing(index_elements=["business_id", "account", "ad_id"])
                )

    async def targetings(self, account: str | None = None) -> list[tuple[str, str, dict[str, Any]]]:
        """Кабинет, кампания и что о её показе известно."""
        statement = (
            select(CAMPAIGNS.c.account, CAMPAIGNS.c.campaign_id, CAMPAIGNS.c.targeting)
            .where(CAMPAIGNS.c.business_id == self._business_id)
            .order_by(CAMPAIGNS.c.account, CAMPAIGNS.c.campaign_id)
        )
        if account is not None:
            statement = statement.where(CAMPAIGNS.c.account == account)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [(row.account, row.campaign_id, dict(row.targeting or {})) for row in rows]

    async def _note(self, target: Any, account: str, which: Any, state: str) -> None:
        statement = (
            target.update()
            .where(target.c.business_id == self._business_id, target.c.account == account, which)
            .values(state=state)
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

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

    async def ads(self, since: date, until: date, scope: Scope) -> list[AdLine]:
        sums = (
            select(AD_STATS.c.account, AD_STATS.c.ad_id, *_sums(AD_STATS))
            .where(
                AD_STATS.c.business_id == self._business_id,
                AD_STATS.c.day >= since,
                AD_STATS.c.day <= until,
            )
            .group_by(AD_STATS.c.account, AD_STATS.c.ad_id)
            .subquery()
        )
        statement = (
            select(
                ADS.c.account,
                ADS.c.ad_id,
                ADS.c.campaign_id,
                ADS.c.title,
                ADS.c.text,
                ADS.c.href,
                ADS.c.state,
                *[func.coalesce(c, 0) for c in list(sums.c)[2:]],
            )
            .outerjoin(sums, (sums.c.account == ADS.c.account) & (sums.c.ad_id == ADS.c.ad_id))
            .where(ADS.c.business_id == self._business_id)
            .order_by(ADS.c.campaign_id, ADS.c.ad_id)
        )
        if scope.account is not None:
            statement = statement.where(ADS.c.account == scope.account)
        if scope.campaign_id is not None:
            statement = statement.where(ADS.c.campaign_id == scope.campaign_id)
        if scope.ad_id is not None:
            statement = statement.where(ADS.c.ad_id == scope.ad_id)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            AdLine(
                account=row[0],
                ad_id=row[1],
                campaign_id=row[2],
                title=row[3],
                text=row[4],
                href=row[5],
                state=row[6],
                totals=_totals(row[7], row[8], row[9], row[10]),
            )
            for row in rows
        ]

    async def days(self, since: date, until: date, scope: Scope) -> list[DayLine]:
        # Объявление — из его отчёта; бизнес и направление — из отчёта кампаний,
        # потому что деньги берём из того же отчёта, что и сводку.
        source = AD_STATS if scope.ad_id is not None else STATS
        statement = (
            select(source.c.day, *_sums(source))
            .where(
                source.c.business_id == self._business_id,
                source.c.day >= since,
                source.c.day <= until,
            )
            .group_by(source.c.day)
            .order_by(source.c.day)
        )
        if scope.account is not None:
            statement = statement.where(source.c.account == scope.account)
        if scope.ad_id is not None:
            statement = statement.where(source.c.ad_id == scope.ad_id)
        elif scope.campaign_id is not None:
            statement = statement.where(source.c.campaign_id == scope.campaign_id)
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
