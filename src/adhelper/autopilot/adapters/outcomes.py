"""Хранилище исходов действий автопилота и чтение статистики для их оценки.

Только запросы одного бизнеса. Оценка идёт по кампании действия: сумма её объявлений
за окно после действия против того же окна на неделю раньше (те же дни недели).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Text,
    Uuid,
    and_,
    column,
    func,
    or_,
    select,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.domain.outcomes import (
    Measured,
    OutcomeRow,
    OutcomeRules,
    OutcomeVerdict,
    PendingOutcome,
    Period,
)
from adhelper.budget.domain.calendar import platform_date

OUTCOMES = table(
    "decision_outcomes",
    column("id", BigInteger),
    column("business_id", Uuid),
    column("account", Text),
    column("change_id", BigInteger),
    column("decision_id", Text),
    column("object_ref", Text),
    column("kind", Text),
    column("applied_at", DateTime(timezone=True)),
    column("evaluate_at", DateTime(timezone=True)),
    column("status", Text),
    column("verdict", Text),
    column("cpl_change_bp", BigInteger),
    column("noise_bp", BigInteger),
    column("measured", JSONB),
    column("rollback_change_id", BigInteger),
    column("note", Text),
    column("rules_version", Text),
    column("evaluated_at", DateTime(timezone=True)),
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
    column("decision_id", Text),
    column("reverts_change_id", BigInteger),
    column("created_at", DateTime(timezone=True)),
)
ADS = table(
    "ads",
    column("business_id", Uuid),
    column("account", Text),
    column("ad_id", Text),
    column("campaign_id", Text),
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
ALERTS = table(
    "alerts",
    column("business_id", Uuid),
    column("kind", Text),
    column("resolved_at", DateTime(timezone=True)),
)
AD_PREFIX = "ad:"
# Что оцениваем: остановки объявлений. Лимиты и переносы трогают общий бюджет, их откат
# отдельное решение с проверкой бюджета, а не автоматика.
EVALUATED_KINDS = ("pause_ad",)
DATA_ALERT = "data_untrusted"


def _period(row: Any) -> Period:
    return Period(int(row.clicks or 0), int(row.spend or 0), int(row.leads or 0))


class SqlOutcomeStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def register_new(self, account: str, rules: OutcomeRules) -> int:
        """Берёт на учёт применённые самостоятельные действия, у которых ещё нет исхода."""
        after_days = rules.window_days + rules.maturity_days
        statement = text(
            "INSERT INTO decision_outcomes (business_id, account, change_id, decision_id,"
            " object_ref, kind, applied_at, evaluate_at)"
            " SELECT c.business_id, c.account, c.id, c.decision_id, c.target_ref, c.kind,"
            " c.created_at, c.created_at + make_interval(days => :days)"
            " FROM changes c WHERE c.business_id = :business AND c.account = :account"
            " AND c.source = 'autopilot' AND c.status = 'applied' AND c.kind = ANY(:kinds)"
            " ON CONFLICT (business_id, change_id) DO NOTHING"
        )
        async with self._sessions.begin() as session:
            result = await session.execute(
                statement,
                {
                    "business": self._business_id,
                    "account": account,
                    "days": after_days,
                    "kinds": list(EVALUATED_KINDS),
                },
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def due(self, account: str, now: datetime) -> Sequence[PendingOutcome]:
        statement = (
            select(
                OUTCOMES.c.id,
                OUTCOMES.c.change_id,
                OUTCOMES.c.object_ref,
                OUTCOMES.c.kind,
                OUTCOMES.c.applied_at,
                OUTCOMES.c.decision_id,
            )
            .where(
                OUTCOMES.c.business_id == self._business_id,
                OUTCOMES.c.account == account,
                OUTCOMES.c.status == "pending",
                OUTCOMES.c.evaluate_at <= now,
            )
            .order_by(OUTCOMES.c.id)
        )
        async with self._sessions() as session:
            return [PendingOutcome(*row) for row in await session.execute(statement)]

    async def measure(self, account: str, pending: PendingOutcome, rules: OutcomeRules) -> Measured:
        applied_day = platform_date(pending.applied_at)
        window = rules.window_days
        after_from, after_to = applied_day + timedelta(days=1), applied_day + timedelta(days=window)
        before_from, before_to = after_from - timedelta(days=7), after_to - timedelta(days=7)
        async with self._sessions() as session:
            campaign = await self._campaign_of(session, account, pending.object_ref)
            if campaign is None:
                return Measured(Period(0, 0, 0), Period(0, 0, 0), confounded=True, reverted=False)
            before = await self._sum(session, account, campaign, before_from, before_to)
            after = await self._sum(session, account, campaign, after_from, after_to)
            confounded = await self._confounded(session, account, campaign, pending, rules)
            reverted = bool(
                await session.scalar(
                    select(func.count()).where(
                        CHANGES.c.business_id == self._business_id,
                        CHANGES.c.reverts_change_id == pending.change_id,
                    )
                )
            )
        return Measured(before, after, confounded, reverted)

    async def _campaign_of(self, session: AsyncSession, account: str, ref: str) -> str | None:
        if not ref.startswith(AD_PREFIX):
            return None
        return await session.scalar(
            select(ADS.c.campaign_id).where(
                ADS.c.business_id == self._business_id,
                ADS.c.account == account,
                ADS.c.ad_id == ref.removeprefix(AD_PREFIX),
            )
        )

    async def _sum(
        self, session: AsyncSession, account: str, campaign: str, first: date, last: date
    ) -> Period:
        statement = (
            select(
                func.coalesce(func.sum(AD_STATS.c.clicks), 0).label("clicks"),
                func.coalesce(func.sum(AD_STATS.c.cost_micros), 0).label("spend"),
                func.coalesce(func.sum(AD_STATS.c.conversions), 0).label("leads"),
            )
            .select_from(
                AD_STATS.join(
                    ADS,
                    and_(
                        ADS.c.business_id == AD_STATS.c.business_id,
                        ADS.c.account == AD_STATS.c.account,
                        ADS.c.ad_id == AD_STATS.c.ad_id,
                    ),
                )
            )
            .where(
                AD_STATS.c.business_id == self._business_id,
                AD_STATS.c.account == account,
                ADS.c.campaign_id == campaign,
                AD_STATS.c.day >= first,
                AD_STATS.c.day <= last,
            )
        )
        return _period((await session.execute(statement)).one())

    async def _confounded(
        self,
        session: AsyncSession,
        account: str,
        campaign: str,
        pending: PendingOutcome,
        rules: OutcomeRules,
    ) -> bool:
        """В окне менялось что-то ещё в этой кампании: её объявления, лимит или показ."""
        ad_refs = select(func.concat(AD_PREFIX, ADS.c.ad_id)).where(
            ADS.c.business_id == self._business_id,
            ADS.c.account == account,
            ADS.c.campaign_id == campaign,
        )
        own_targets = or_(
            CHANGES.c.target_ref == campaign,
            CHANGES.c.target_ref == f"targeting:{campaign}",
            CHANGES.c.target_ref.in_(ad_refs),
        )
        end = pending.applied_at + timedelta(days=rules.window_days)
        count = await session.scalar(
            select(func.count()).where(
                CHANGES.c.business_id == self._business_id,
                CHANGES.c.account == account,
                CHANGES.c.status == "applied",
                CHANGES.c.id != pending.change_id,
                CHANGES.c.created_at > pending.applied_at,
                CHANGES.c.created_at <= end,
                own_targets,
            )
        )
        return bool(count)

    async def save(
        self,
        outcome_id: int,
        *,
        status: str,
        verdict: OutcomeVerdict | None,
        cpl_change_bp: int | None,
        noise_bp: int | None,
        measured: Mapping[str, Any] | None,
        rollback_change_id: int | None,
        note: str,
        rules_version: str,
        at: datetime,
    ) -> None:
        statement = (
            OUTCOMES.update()
            .where(OUTCOMES.c.business_id == self._business_id, OUTCOMES.c.id == outcome_id)
            .values(
                status=status,
                verdict=verdict.value if verdict else None,
                cpl_change_bp=cpl_change_bp,
                noise_bp=noise_bp,
                measured=dict(measured) if measured else None,
                rollback_change_id=rollback_change_id,
                note=note,
                rules_version=rules_version,
                evaluated_at=at,
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def recent_verdicts(self, account: str, limit: int) -> Sequence[OutcomeVerdict]:
        statement = (
            select(OUTCOMES.c.verdict)
            .where(
                OUTCOMES.c.business_id == self._business_id,
                OUTCOMES.c.account == account,
                OUTCOMES.c.status == "evaluated",
                OUTCOMES.c.verdict.is_not(None),
            )
            .order_by(OUTCOMES.c.evaluated_at.desc(), OUTCOMES.c.id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            return [OutcomeVerdict(value) for value in await session.scalars(statement)]

    async def rollbacks_since(self, account: str, since: datetime) -> int:
        statement = select(func.count()).where(
            OUTCOMES.c.business_id == self._business_id,
            OUTCOMES.c.account == account,
            OUTCOMES.c.rollback_change_id.is_not(None),
            OUTCOMES.c.evaluated_at >= since,
        )
        async with self._sessions() as session:
            return int(await session.scalar(statement) or 0)

    async def data_untrusted(self) -> bool:
        statement = select(func.count()).where(
            ALERTS.c.business_id == self._business_id,
            ALERTS.c.kind == DATA_ALERT,
            ALERTS.c.resolved_at.is_(None),
        )
        async with self._sessions() as session:
            return bool(await session.scalar(statement))

    async def autopilot_changes_since(self, account: str, since: datetime) -> int:
        """Самостоятельные действия за период; откаты считаются отдельно."""
        statement = select(func.count()).where(
            CHANGES.c.business_id == self._business_id,
            CHANGES.c.account == account,
            CHANGES.c.source == "autopilot",
            CHANGES.c.status == "applied",
            CHANGES.c.created_at >= since,
        )
        async with self._sessions() as session:
            return int(await session.scalar(statement) or 0)

    async def evaluated_since(self, account: str, since: datetime) -> Sequence[OutcomeRow]:
        statement = (
            select(
                OUTCOMES.c.id,
                OUTCOMES.c.change_id,
                OUTCOMES.c.object_ref,
                OUTCOMES.c.kind,
                OUTCOMES.c.verdict,
                OUTCOMES.c.cpl_change_bp,
                OUTCOMES.c.noise_bp,
                OUTCOMES.c.rollback_change_id,
                OUTCOMES.c.note,
                OUTCOMES.c.evaluated_at,
                OUTCOMES.c.measured,
            )
            .where(
                OUTCOMES.c.business_id == self._business_id,
                OUTCOMES.c.account == account,
                OUTCOMES.c.status == "evaluated",
                OUTCOMES.c.evaluated_at >= since,
            )
            .order_by(OUTCOMES.c.id.desc())
        )
        async with self._sessions() as session:
            return [OutcomeRow(*row) for row in await session.execute(statement)]


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC) - timedelta(days=1)
