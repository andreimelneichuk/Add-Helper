"""Срывы кабинета по журналу: от них зависит, откроется ли следующая ступень доверия."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.shadow_report import AUTO_ROLLBACK_DECISION

BUDGET_ALERTS = ("budget_stopped", "budget_stop_pending", "balance_mismatch")
DEMOTION_PREFIX = "Выключил самостоятельный режим%"


class SqlTrustFacts:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], business_id: UUID) -> None:
        self._sessions = sessions
        self._business_id = business_id

    async def failures_since(self, since: datetime) -> int:
        """Срыв: автооткат, сбой действия автопилота, срыв бюджета, снижение до «Предлагает»."""
        params: dict[str, Any] = {"b": self._business_id, "s": since}
        statements: tuple[tuple[str, dict[str, Any]], ...] = (
            (
                "SELECT count(*) FROM changes WHERE business_id = :b AND created_at >= :s"
                " AND decision_id = :d AND status IN ('applied', 'failed', 'rejected', 'conflict')",
                {"d": AUTO_ROLLBACK_DECISION},
            ),
            (
                "SELECT count(*) FROM changes WHERE business_id = :b AND created_at >= :s"
                " AND source = 'autopilot' AND status IN ('failed', 'rejected', 'conflict')",
                {},
            ),
            (
                # Тревога, начавшаяся до окна и не снятая, тоже срыв: бюджет остановлен сейчас.
                "SELECT count(*) FROM alerts WHERE business_id = :b"
                " AND (resolved_at IS NULL OR resolved_at >= :s) AND kind = ANY(:kinds)",
                {"kinds": list(BUDGET_ALERTS)},
            ),
            (
                "SELECT count(*) FROM autopilot_settings_log WHERE business_id = :b"
                " AND created_at >= :s AND text LIKE :p",
                {"p": DEMOTION_PREFIX},
            ),
        )
        total = 0
        async with self._sessions() as session:
            for sql, extra in statements:
                total += int(await session.scalar(text(sql), params | extra) or 0)
        return total
