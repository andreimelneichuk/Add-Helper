"""Метрики недели по одному бизнесу. Только чтение; каждый запрос ограничен business_id."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.domain.shadow_report import WeekMetrics, count_divergence

AUTO_ROLLBACK_DECISION = "outcome-rollback"
ACCEPT_WINDOW = timedelta(days=7)
# Ответ владельца на предложение теста; для остальных действий принятие видно в журнале.
SHADOW_PREFIX = "shadow:"


@dataclass(frozen=True)
class SampleRow:
    id: int
    business_id: UUID
    object_ref: str
    action: str | None
    status: str
    rules_version: str
    decided_at: datetime


class SqlShadowReport:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def _scalar(self, session: AsyncSession, sql: str, **params: Any) -> int:
        value = await session.scalar(text(sql), {"b": self._business_id, **params})
        return int(value or 0)

    async def metrics(
        self, start: datetime, end: datetime, ticks: tuple[int, int] = (0, 0)
    ) -> WeekMetrics:
        window = {"a": start, "z": end}
        async with self._sessions() as session:
            proposals = await self._scalar(
                session,
                "SELECT count(*) FROM decisions WHERE business_id = :b AND status ="
                " 'awaiting_confirmation' AND action IS NOT NULL AND decided_at >= :a"
                " AND decided_at < :z",
                **window,
            )
            accepted = await self._scalar(
                session,
                """
                SELECT count(*) FROM decisions p WHERE p.business_id = :b
                  AND p.status = 'awaiting_confirmation' AND p.action IS NOT NULL
                  AND p.decided_at >= :a AND p.decided_at < :z
                  AND (
                    EXISTS (SELECT 1 FROM decisions d WHERE d.business_id = p.business_id
                      AND d.account = p.account AND d.object_ref = p.object_ref
                      AND d.action = p.action AND d.decided_at > p.decided_at
                      AND d.status = 'prepared')
                    OR EXISTS (SELECT 1 FROM changes c WHERE c.business_id = p.business_id
                      AND c.target_ref = p.object_ref AND c.source = 'user'
                      AND c.status = 'applied' AND c.created_at > p.decided_at
                      AND c.created_at <= p.decided_at + :win
                      AND c.kind = CASE p.action WHEN 'pause_ad' THEN 'pause_ad'
                        WHEN 'increase_limit' THEN 'set_limit' ELSE '' END)
                  )
                """,
                win=ACCEPT_WINDOW,
                **window,
            )
            auto_actions = await self._scalar(
                session,
                "SELECT count(*) FROM changes WHERE business_id = :b AND source = 'autopilot'"
                " AND status = 'applied' AND created_at >= :a AND created_at < :z",
                **window,
            )
            rollbacks = await self._scalar(
                session,
                "SELECT count(*) FROM changes WHERE business_id = :b AND decision_id = :d"
                " AND status = 'applied' AND created_at >= :a AND created_at < :z",
                d=AUTO_ROLLBACK_DECISION,
                **window,
            )
            judged = await self._scalar(
                session,
                "SELECT count(*) FROM decision_outcomes WHERE business_id = :b"
                " AND status = 'evaluated' AND evaluated_at >= :a AND evaluated_at < :z",
                **window,
            )
            worse = await self._scalar(
                session,
                "SELECT count(*) FROM decision_outcomes WHERE business_id = :b"
                " AND status = 'evaluated' AND verdict = 'worse'"
                " AND evaluated_at >= :a AND evaluated_at < :z",
                **window,
            )
            insufficient = await self._scalar(
                session,
                "SELECT count(*) FROM decision_outcomes WHERE business_id = :b"
                " AND verdict = 'insufficient' AND evaluated_at >= :a AND evaluated_at < :z",
                **window,
            )
            reviewed = await self._scalar(
                session,
                "SELECT count(*) FROM decisions WHERE business_id = :b AND status <> 'shadow'"
                " AND inputs ->> 'review' IS NOT NULL AND inputs ->> 'review' <> 'off'"
                " AND decided_at >= :a AND decided_at < :z",
                **window,
            )
            pairs, diverged = await self._divergence(session, start, end)
        return WeekMetrics(
            proposals=proposals,
            accepted=accepted,
            auto_actions=auto_actions,
            auto_rollbacks=rollbacks,
            judged=judged,
            worse=worse,
            insufficient=insufficient,
            shadow_pairs=pairs,
            diverged=diverged,
            ticks_expected=ticks[0],
            ticks_missed=ticks[1],
            reviewed_actions=reviewed,
        )

    async def _divergence(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> tuple[int, int]:
        """Последнее рабочее и последнее теневое решение по объявлению за каждые сутки (UTC)."""
        rows = (
            await session.execute(
                text(
                    """
                    SELECT DISTINCT ON (object_ref, status = 'shadow', d)
                      object_ref, status = 'shadow' AS is_shadow, action,
                      (decided_at AT TIME ZONE 'UTC')::date::text AS d
                    FROM decisions
                    WHERE business_id = :b AND decided_at >= :a AND decided_at < :z
                    ORDER BY object_ref, status = 'shadow', d, decided_at DESC, id DESC
                    """
                ),
                {"b": self._business_id, "a": start, "z": end},
            )
        ).all()
        working: dict[tuple[str, str], str] = {}
        shadow: dict[tuple[str, str], str] = {}
        for ref, is_shadow, action, day in rows:
            if is_shadow:
                shadow[(ref.removeprefix(SHADOW_PREFIX), day)] = action or "wait"
            else:
                working[(ref, day)] = action or "wait"
        return count_divergence(working, shadow)

    async def sample_pool(self, start: datetime, end: datetime) -> list[SampleRow]:
        """Решения недели, которые стоит разбирать глазами: с действием, рабочие и теневые."""
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, business_id, object_ref, action, status, rules_version,"
                        " decided_at FROM decisions WHERE business_id = :b AND action IS NOT NULL"
                        " AND decided_at >= :a AND decided_at < :z ORDER BY id"
                    ),
                    {"b": self._business_id, "a": start, "z": end},
                )
            ).all()
        return [SampleRow(*row) for row in rows]
