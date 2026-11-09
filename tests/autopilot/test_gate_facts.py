"""Факты ворот из журнала: счётчики дня и недели, последнее изменение лимита, рабочий бюджет."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.gate_facts import SqlGateFacts
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.ledger.adapters.sql import SqlChangeRepository
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
Sessions = async_sessionmaker[AsyncSession]


def proposed(
    source: ChangeSource, kind: ChangeKind, ref: str, limit: int, key: str
) -> ProposedChange:
    return ProposedChange("acc", source, kind, ref, {"limit": limit}, "тест", key)


async def test_counts_autopilot_changes_and_moved_money(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    repository = SqlChangeRepository(sessions, BUSINESS)
    applied = ChangeStatus.APPLIED
    await repository.add(
        proposed(ChangeSource.AUTOPILOT, ChangeKind.SET_LIMIT, "c1", 150, "k1"),
        {"limit": 100},
        applied,
    )
    await repository.add(
        proposed(ChangeSource.AUTOPILOT, ChangeKind.SET_LIMIT, "c2", 80, "k2"),
        {"limit": 100},
        applied,
    )
    await repository.add(
        proposed(ChangeSource.USER, ChangeKind.SET_LIMIT, "c3", 500, "k3"), {"limit": 100}, applied
    )

    facts = await SqlGateFacts(sessions, BUSINESS, SqlPeriodStore(sessions)).load(
        "acc", datetime.now(UTC)
    )

    assert facts.changes_today == 2
    # Растёт только то, что выросло: снижение донора деньги не двигает.
    assert facts.moved_this_week == 50
    assert set(facts.last_limit_change) == {"c1", "c2", "c3"}
    # Периода нет: рабочий бюджет нулевой, рост лимита не пройдёт, а не разрешён по умолчанию.
    assert facts.operating_budget == 0
    assert facts.api_points_rest is None


async def test_other_business_has_no_counters(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    other = uuid.uuid4()
    await seed_account(sessions, other, "acc2")
    await SqlChangeRepository(sessions, BUSINESS).add(
        proposed(ChangeSource.AUTOPILOT, ChangeKind.SET_LIMIT, "c1", 150, "k1"),
        {"limit": 100},
        ChangeStatus.APPLIED,
    )

    facts = await SqlGateFacts(sessions, other, SqlPeriodStore(sessions)).load(
        "acc", datetime.now(UTC)
    )

    assert (facts.changes_today, facts.moved_this_week, dict(facts.last_limit_change)) == (0, 0, {})
