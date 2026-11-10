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
    AUTO_ROLLBACK_DECISION,
    ROLLBACK_STARTED,
    Measured,
    OutcomeRow,
    OutcomeRules,
    OutcomeVerdict,
    PendingOutcome,
    Period,
)
from adhelper.budget.domain.calendar import moscow_period, platform_date

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
    column("claimed_at", DateTime(timezone=True)),
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
# Захваченный исход считается брошенным, если за это время его не записали: воркер упал.
CLAIM_LEASE = timedelta(minutes=30)


def _period(row: Any) -> Period:
    return Period(int(row.clicks or 0), int(row.spend or 0), int(row.leads or 0))


class SqlOutcomeStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def register_new(self, account: str, rules: OutcomeRules, now: datetime) -> int:
        """Берёт на учёт применённые самостоятельные действия, у которых ещё нет исхода.

        Старше `register_lookback_days` не берём: после ввода этапа или простоя автопилот не
        откатывает пачкой остановки, которые владелец давно видел. Срок оценки — полночь по
        Москве после дня, когда дозрело последнее окно: окно считается по дням площадки.
        """
        after_days = rules.window_days + rules.maturity_days
        statement = text(
            "INSERT INTO decision_outcomes (business_id, account, change_id, decision_id,"
            " object_ref, kind, applied_at, evaluate_at)"
            " SELECT c.business_id, c.account, c.id, c.decision_id, c.target_ref, c.kind,"
            " c.created_at,"
            " ((c.created_at AT TIME ZONE 'Europe/Moscow')::date + CAST(:days AS integer))"
            "::timestamp AT TIME ZONE 'Europe/Moscow'"
            " FROM changes c WHERE c.business_id = :business AND c.account = :account"
            " AND c.source = 'autopilot' AND c.status = 'applied' AND c.kind = ANY(:kinds)"
            " AND c.created_at >= :since"
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
                    "since": now - timedelta(days=rules.register_lookback_days),
                },
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def claim_due(self, account: str, now: datetime) -> Sequence[PendingOutcome]:
        """Захватывает созревшие исходы одним запросом: второй воркер их не получит.

        Забирается и «оценивается» с истёкшей арендой: воркер, который брал запись, упал.
        """
        statement = text(
            "UPDATE decision_outcomes SET status = 'evaluating', claimed_at = :now"
            " WHERE id IN (SELECT id FROM decision_outcomes"
            " WHERE business_id = :business AND account = :account AND evaluate_at <= :now"
            " AND (status = 'pending' OR (status = 'evaluating' AND claimed_at < :stale))"
            " ORDER BY id FOR UPDATE SKIP LOCKED)"
            " RETURNING id, change_id, object_ref, kind, applied_at, decision_id, evaluate_at, note"
        )
        async with self._sessions.begin() as session:
            rows = (
                await session.execute(
                    statement,
                    {
                        "business": self._business_id,
                        "account": account,
                        "now": now,
                        "stale": now - CLAIM_LEASE,
                    },
                )
            ).all()
        return sorted((PendingOutcome(*row) for row in rows), key=lambda item: item.id)

    async def release(self, outcome_id: int, note: str) -> None:
        """Вернуть захваченный исход в очередь: оценим на следующем часовом тике."""
        statement = (
            OUTCOMES.update()
            .where(
                OUTCOMES.c.business_id == self._business_id,
                OUTCOMES.c.id == outcome_id,
                OUTCOMES.c.status == "evaluating",
            )
            .values(status="pending", claimed_at=None, note=note)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def mark_rollback_started(self, outcome_id: int) -> None:
        statement = (
            OUTCOMES.update()
            .where(
                OUTCOMES.c.business_id == self._business_id,
                OUTCOMES.c.id == outcome_id,
                OUTCOMES.c.status == "evaluating",
            )
            .values(note=ROLLBACK_STARTED)
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def measure(
        self, account: str, pending: PendingOutcome, rules: OutcomeRules, now: datetime
    ) -> Measured:
        """Окна считаются по дням площадки (МСК), как и окно вмешательств.

        «После» — дни действия +1…+window. «До» — те же дни неделей раньше, то есть с дня
        действия −6: день действия входит в «до» целиком (пауза ставится в течение дня, до неё
        день шёл как обычно, после — с меньшим расходом; цена обращения от этого почти не
        меняется). Недозревшие дни (последние `maturity_days`) в суммы не входят, а окно
        «после» полное, только если за каждый его день есть статистика.
        """
        applied_day = platform_date(pending.applied_at)
        window = rules.window_days
        after_from, after_to = applied_day + timedelta(days=1), applied_day + timedelta(days=window)
        before_from, before_to = after_from - timedelta(days=7), after_to - timedelta(days=7)
        mature_to = platform_date(now) - timedelta(days=rules.maturity_days)
        async with self._sessions() as session:
            campaign = await self._campaign_of(session, account, pending.object_ref)
            if campaign is None:
                return Measured(
                    Period(0, 0, 0),
                    Period(0, 0, 0),
                    confounded=True,
                    reverted=False,
                    complete=False,
                )
            before, _ = await self._sum(
                session, account, campaign, before_from, before_to, mature_to
            )
            after, after_days = await self._sum(
                session, account, campaign, after_from, after_to, mature_to
            )
            confounded = await self._confounded(
                session, account, campaign, pending, before_from, after_to
            )
            reverted = bool(
                await session.scalar(
                    select(func.count()).where(
                        CHANGES.c.business_id == self._business_id,
                        CHANGES.c.reverts_change_id == pending.change_id,
                        CHANGES.c.status == "applied",
                    )
                )
            )
        complete = after_to <= mature_to and after_days >= window
        return Measured(before, after, confounded, reverted, complete)

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
        self,
        session: AsyncSession,
        account: str,
        campaign: str,
        first: date,
        last: date,
        mature_to: date,
    ) -> tuple[Period, int]:
        """Сумма кампании за дозревшие дни окна и число дней, за которые есть статистика."""
        statement = (
            select(
                func.coalesce(func.sum(AD_STATS.c.clicks), 0).label("clicks"),
                func.coalesce(func.sum(AD_STATS.c.cost_micros), 0).label("spend"),
                func.coalesce(func.sum(AD_STATS.c.conversions), 0).label("leads"),
                func.count(func.distinct(AD_STATS.c.day)).label("days"),
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
                AD_STATS.c.day <= min(last, mature_to),
            )
        )
        row = (await session.execute(statement)).one()
        return _period(row), int(row.days or 0)

    async def _confounded(
        self,
        session: AsyncSession,
        account: str,
        campaign: str,
        pending: PendingOutcome,
        first_day: date,
        last_day: date,
    ) -> bool:
        """В окнах «до» и «после» менялось что-то ещё в этой кампании: объявления, лимит, показ.

        Границы те же, что у статистики: сутки по Москве от первого дня окна «до» до конца
        последнего дня окна «после».
        """
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
        starts_at, ends_at = moscow_period(first_day, (last_day - first_day).days + 1)
        count = await session.scalar(
            select(func.count()).where(
                CHANGES.c.business_id == self._business_id,
                CHANGES.c.account == account,
                CHANGES.c.status == "applied",
                CHANGES.c.id != pending.change_id,
                CHANGES.c.created_at >= starts_at,
                CHANGES.c.created_at < ends_at,
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
    ) -> bool:
        """Пишет только захваченный исход: если аренда ушла к другому воркеру, не трогает."""
        statement = (
            OUTCOMES.update()
            .where(
                OUTCOMES.c.business_id == self._business_id,
                OUTCOMES.c.id == outcome_id,
                OUTCOMES.c.status == "evaluating",
            )
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
                claimed_at=None,
            )
        )
        async with self._sessions.begin() as session:
            result = await session.execute(statement)
        return bool(getattr(result, "rowcount", 0))

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
        """Автооткаты по журналу: записи отката с меткой автопилота, созданные с `since`.

        Считаем по журналу, а не по строкам исходов: откат уже сделан на площадке, даже если
        воркер упал до записи исхода. Откаты в полёте (запланирован, выполняется) тоже в счёте:
        при недоступной площадке иначе можно превысить предел. Ручные отмены владельца не
        считаем: предохранитель про решения автопилота, а не про действия владельца.
        """
        statement = text(
            "SELECT count(*) FROM changes r JOIN changes o"
            " ON o.id = r.reverts_change_id AND o.business_id = r.business_id"
            " WHERE r.business_id = :business AND r.account = :account"
            " AND r.source = 'rollback' AND r.status IN ('planned', 'applying', 'applied')"
            " AND r.decision_id = :decision AND r.created_at >= :since"
            " AND o.source = 'autopilot'"
        )
        async with self._sessions() as session:
            return int(
                await session.scalar(
                    statement,
                    {
                        "business": self._business_id,
                        "account": account,
                        "since": since,
                        "decision": AUTO_ROLLBACK_DECISION,
                    },
                )
                or 0
            )

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
