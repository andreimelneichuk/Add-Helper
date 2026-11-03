"""Данные цикла автопилота из витрины: отчёты по объявлениям, лимиты, журнал и решения.

Только SELECT и только одного бизнеса. docs/04-autopilot.md §3–5.

Чего витрина пока не знает, то заменено осторожным значением, а не догадкой:
- группы объявлений: каждое объявление считается последним в своей группе, поэтому
  автопилот не выключает объявления, пока отчёт не принесёт группы (T5);
- ограничение кампании бюджетом: «нет», поэтому лимит сам не растёт;
- визиты: нет источника (Метрика, T6), проверки визитов пропускаются.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import BigInteger, Date, DateTime, Text, Uuid, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.domain.health import Window
from adhelper.autopilot.domain.model import (
    AccountData,
    AdDay,
    AdStats,
    CampaignStats,
    DailyStat,
    Day,
    History,
    mature_totals,
)
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.budget.domain.calendar import platform_date

ADS = table(
    "ads",
    column("business_id", Uuid),
    column("account", Text),
    column("ad_id", Text),
    column("campaign_id", Text),
    column("state", Text),
)
AD_STATS = table(
    "ad_stats",
    column("business_id", Uuid),
    column("account", Text),
    column("ad_id", Text),
    column("day", Date),
    column("clicks", BigInteger),
    column("cost_micros", BigInteger),
    column("conversions", BigInteger),
)
CAMPAIGNS = table(
    "campaigns",
    column("business_id", Uuid),
    column("account", Text),
    column("campaign_id", Text),
    column("state", Text),
    column("limit_micros", BigInteger),
)
CHANGES = table(
    "changes",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("account", Text),
    column("source", Text),
    column("kind", Text),
    column("target_ref", Text),
    column("status", Text),
    column("reverts_change_id", BigInteger),
    column("created_at", DateTime(timezone=True)),
)
DECISIONS = table(
    "decisions",
    column("business_id", Uuid),
    column("account", Text),
    column("object_ref", Text),
    column("status", Text),
    column("decided_at", DateTime(timezone=True)),
)
EXPERIMENTS = table(
    "experiments",
    column("business_id", Uuid),
    column("account", Text),
    column("campaign_id", Text),
    column("status", Text),
)
ALERTS = table(
    "alerts",
    column("business_id", Uuid),
    column("kind", Text),
    column("resolved_at", DateTime(timezone=True)),
)
ON = "on"
AD_PREFIX = "ad:"
TARGETING = "targeting:"
OPEN_TESTS = ("review", "running", "paused")
DECLINED = "declined"
INCIDENT = "platform_unavailable"


GOALS = table(
    "lead_goals",
    column("business_id", Uuid),
    column("goal_micros", BigInteger),
)
ACCOUNTS = table(
    "ad_accounts",
    column("business_id", Uuid),
    column("external_login", Text),
)


async def autopilot_accounts(
    sessions: async_sessionmaker[AsyncSession],
) -> list[tuple[UUID, str, int]]:
    """Кабинеты с целью цены обращения: без цели автопилоту не с чем сравнивать."""
    statement = (
        select(ACCOUNTS.c.business_id, ACCOUNTS.c.external_login, GOALS.c.goal_micros)
        .join(GOALS, GOALS.c.business_id == ACCOUNTS.c.business_id)
        .where(GOALS.c.goal_micros > 0)
        .order_by(ACCOUNTS.c.business_id, ACCOUNTS.c.external_login)
    )
    async with sessions() as session:
        return [(b, login, int(goal)) for b, login, goal in (await session.execute(statement))]


def _window(rows: list[DailyStat]) -> Window:
    return Window(
        sum(r.spend for r in rows), sum(r.leads for r in rows), sum(r.clicks for r in rows), None
    )


class SqlAutopilotData:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        business_id: UUID,
        rules: AutopilotRules,
    ) -> None:
        self._sessions = sessions
        self._business_id = business_id
        self._rules = rules

    async def load(self, account: str, now: datetime) -> AccountData:
        own_ads = (ADS.c.business_id == self._business_id) & (ADS.c.account == account)
        own_stats = (AD_STATS.c.business_id == self._business_id) & (AD_STATS.c.account == account)
        own_campaigns = (CAMPAIGNS.c.business_id == self._business_id) & (
            CAMPAIGNS.c.account == account
        )
        async with self._sessions() as session:
            ads = (
                await session.execute(
                    select(ADS.c.ad_id, ADS.c.campaign_id, ADS.c.state).where(own_ads)
                )
            ).all()
            stats = (
                await session.execute(
                    select(
                        AD_STATS.c.ad_id,
                        AD_STATS.c.day,
                        AD_STATS.c.cost_micros,
                        AD_STATS.c.conversions,
                        AD_STATS.c.clicks,
                    ).where(own_stats)
                )
            ).all()
            campaigns = (
                await session.execute(
                    select(
                        CAMPAIGNS.c.campaign_id, CAMPAIGNS.c.state, CAMPAIGNS.c.limit_micros
                    ).where(own_campaigns)
                )
            ).all()
            changed = await self._last_changes(session, account)
            rolled_back = await self._rolled_back(session, account)
            declined = await self._declined(session, account, now)
            testing = set(
                await session.scalars(
                    select(EXPERIMENTS.c.campaign_id).where(
                        EXPERIMENTS.c.business_id == self._business_id,
                        EXPERIMENTS.c.account == account,
                        EXPERIMENTS.c.status.in_(OPEN_TESTS),
                    )
                )
            )
            incident = await session.scalar(
                select(func.count()).where(
                    ALERTS.c.business_id == self._business_id,
                    ALERTS.c.kind == INCIDENT,
                    ALERTS.c.resolved_at.is_(None),
                )
            )

        rows: dict[str, list[DailyStat]] = defaultdict(list)
        for ad_id, day, cost, conversions, clicks in stats:
            rows[ad_id].append(DailyStat(day, int(cost), int(conversions), int(clicks), 0))
        running = {cid for cid, state, _ in campaigns if state == ON}
        today = platform_date(now)
        ad_stats = []
        for ad_id, campaign_id, state in ads:
            ref = AD_PREFIX + ad_id
            totals = mature_totals(rows[ad_id], now, self._rules)
            first = min((r.day for r in rows[ad_id]), default=today)
            ad_stats.append(
                AdStats(
                    ref,
                    campaign_id,
                    # Групп витрина не знает: объявление — сама себе группа, см. выше.
                    ref,
                    totals.spend,
                    totals.leads,
                    (today - first).days,
                    state == ON and campaign_id in running,
                    rollback_hold=ref in rolled_back,
                    test_hold=ref in declined,
                )
            )
        # Изменение направления — это его лимит, показ или любое его объявление.
        owner = {AD_PREFIX + ad_id: cid for ad_id, cid, _ in ads}
        for cid, _, _ in campaigns:
            owner[cid] = cid
            owner[TARGETING + cid] = cid
        last_change: dict[str, datetime] = {}
        for ref, at in changed.items():
            cid = owner.get(ref)
            if cid is not None and (cid not in last_change or at > last_change[cid]):
                last_change[cid] = at
        campaign_stats = [
            CampaignStats(cid, int(limit or 0), False, last_change.get(cid), cid in testing)
            for cid, _, limit in campaigns
        ]
        boundary = today - timedelta(days=self._rules.health_recent_days)
        every = [row for ad_rows in rows.values() for row in ad_rows]
        return AccountData(
            ad_stats,
            campaign_stats,
            _window([r for r in every if r.day > boundary]),
            _window([r for r in every if r.day <= boundary]),
            bool(incident),
            False,
        )

    async def _last_changes(self, session: AsyncSession, account: str) -> dict[str, datetime]:
        """Когда последний раз меняли каждый объект: любые применённые изменения, не только наши."""
        statement = (
            select(CHANGES.c.target_ref, func.max(CHANGES.c.created_at))
            .where(
                CHANGES.c.business_id == self._business_id,
                CHANGES.c.account == account,
                CHANGES.c.status == "applied",
            )
            .group_by(CHANGES.c.target_ref)
        )
        return {ref: at for ref, at in (await session.execute(statement)).all()}

    async def _rolled_back(self, session: AsyncSession, account: str) -> set[str]:
        """Решения автопилота, которые отменил владелец: сам автопилот их не повторит."""
        reverts = CHANGES.alias("reverts")
        statement = select(CHANGES.c.target_ref).where(
            CHANGES.c.business_id == self._business_id,
            CHANGES.c.account == account,
            CHANGES.c.source == "autopilot",
            select(reverts.c.id)
            .where(
                reverts.c.business_id == self._business_id,
                reverts.c.reverts_change_id == CHANGES.c.id,
            )
            .exists(),
        )
        return set(await session.scalars(statement))

    async def _declined(self, session: AsyncSession, account: str, now: datetime) -> set[str]:
        since = now - timedelta(days=self._rules.test_decline_hold_days)
        statement = select(DECISIONS.c.object_ref).where(
            DECISIONS.c.business_id == self._business_id,
            DECISIONS.c.account == account,
            DECISIONS.c.status == DECLINED,
            DECISIONS.c.decided_at > since,
        )
        return set(await session.scalars(statement))


class SqlHistory:
    """Записанные дни кабинета для симулятора: те же подмены, что у суточного цикла.

    Объявление — своя группа, визиты неизвестны, сбоев площадки в прошлом не отмечаем.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def load(self, account: str) -> History:
        own_ads = (ADS.c.business_id == self._business_id) & (ADS.c.account == account)
        statement = (
            select(
                AD_STATS.c.day,
                AD_STATS.c.ad_id,
                ADS.c.campaign_id,
                AD_STATS.c.cost_micros,
                AD_STATS.c.conversions,
                AD_STATS.c.clicks,
            )
            .join(
                ADS,
                (ADS.c.business_id == AD_STATS.c.business_id)
                & (ADS.c.account == AD_STATS.c.account)
                & (ADS.c.ad_id == AD_STATS.c.ad_id),
            )
            .where(own_ads)
            .order_by(AD_STATS.c.day, AD_STATS.c.ad_id)
        )
        limits_statement = select(CAMPAIGNS.c.campaign_id, CAMPAIGNS.c.limit_micros).where(
            (CAMPAIGNS.c.business_id == self._business_id) & (CAMPAIGNS.c.account == account)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            limits = {cid: int(limit) for cid, limit in await session.execute(limits_statement)}
        by_day: dict[date, list[AdDay]] = defaultdict(list)
        for day, ad_id, campaign_id, cost, conversions, clicks in rows:
            ref = f"{AD_PREFIX}{ad_id}"
            by_day[day].append(
                AdDay(ref, campaign_id, ref, int(cost), int(conversions), int(clicks), None)
            )
        days = [Day(day, ads) for day, ads in by_day.items()]
        return History(days, limits)
