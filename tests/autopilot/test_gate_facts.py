"""Факты ворот из журнала: счётчики дня и недели, последнее изменение лимита, рабочий бюджет."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.gate_facts import SqlGateFacts
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.ledger.adapters.sql import ChangeRow, SqlChangeRepository
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


async def put(sessions: Sessions, ref: str, at: datetime, key: str) -> None:
    """Запись с заданным временем: менять журнал нельзя, поэтому вставляем сразу с ним."""
    async with sessions.begin() as session:
        await session.execute(
            insert(ChangeRow).values(
                business_id=BUSINESS, account="acc", source=ChangeSource.AUTOPILOT.value,
                kind=ChangeKind.SET_LIMIT.value, target_ref=ref, before={"limit": 100},
                planned={"limit": 150}, status=ChangeStatus.APPLIED.value, explanation="тест",
                idempotency_key=key, created_at=at, updated_at=at,
            )
        )  # fmt: skip


async def test_transfer_records_count_as_two_changes_and_moved_money(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    repository = SqlChangeRepository(sessions, BUSINESS)
    # Перенос идёт под источником стража бюджета, но под идентификатором переноса.
    for ref, limit, before, key in (("a", 450, 500, "t1"), ("b", 150, 100, "t2")):
        await repository.add(
            ProposedChange(
                "acc", ChangeSource.BUDGET_GUARD, ChangeKind.SET_LIMIT, ref, {"limit": limit},
                "перенос", key, decision_id="transfer:x",
            ),
            {"limit": before},
            ChangeStatus.APPLIED,
        )  # fmt: skip

    facts = await SqlGateFacts(sessions, BUSINESS, SqlPeriodStore(sessions)).load(
        "acc", datetime.now(UTC)
    )

    assert facts.changes_today == 2
    assert facts.moved_this_week == 50


async def test_pending_and_reverting_changes_count(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    repository = SqlChangeRepository(sessions, BUSINESS)
    first = await repository.add(
        proposed(ChangeSource.AUTOPILOT, ChangeKind.SET_LIMIT, "c1", 150, "k1"),
        {"limit": 100},
        ChangeStatus.PLANNED,
    )
    await repository.add(
        ProposedChange(
            "acc", ChangeSource.AUTOPILOT, ChangeKind.SET_LIMIT, "c2", {"limit": 100},
            "откат", "k2", reverts_change_id=first.id,
        ),
        {"limit": 130},
        ChangeStatus.APPLIED,
    )  # fmt: skip
    await repository.add(
        proposed(ChangeSource.AUTOPILOT, ChangeKind.SET_LIMIT, "c3", 120, "k3"),
        {"limit": 100},
        ChangeStatus.FAILED,
    )

    facts = await SqlGateFacts(sessions, BUSINESS, SqlPeriodStore(sessions)).load(
        "acc", datetime.now(UTC)
    )

    # Незавершённое и откат — изменения; упавшее ничего на площадке не изменило.
    assert facts.changes_today == 2
    assert facts.moved_this_week == 50


async def test_old_changes_only_set_the_last_limit_change(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=30)
    await put(sessions, "c1", old, "k1")

    facts = await SqlGateFacts(sessions, BUSINESS, SqlPeriodStore(sessions)).load("acc", now)

    assert (facts.changes_today, facts.moved_this_week) == (0, 0)
    assert facts.last_limit_change == {"c1": old}


async def test_moscow_midnight_splits_the_day(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    # Полночь МСК 10 ноября — это 21:00 UTC 9 ноября.
    midnight = datetime(2026, 11, 9, 21, 0, tzinfo=UTC)
    await put(sessions, "c1", midnight - timedelta(seconds=1), "k1")
    await put(sessions, "c2", midnight, "k2")

    facts = await SqlGateFacts(sessions, BUSINESS, SqlPeriodStore(sessions)).load(
        "acc", midnight + timedelta(hours=3)
    )

    assert facts.changes_today == 1


async def test_expired_period_is_not_a_working_budget(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    store = SqlPeriodStore(sessions)
    ended = BudgetPeriod(100, 0, now - timedelta(days=40), now - timedelta(days=10))
    await store.add(BUSINESS, "acc", ended, "budget-1", None, now)

    facts = await SqlGateFacts(sessions, BUSINESS, store).load("acc", now)

    # Просроченный период: рабочий бюджет неизвестен, рост лимита не пройдёт.
    assert facts.operating_budget == 0
    current = BudgetPeriod(2_000, 0, now - timedelta(days=1), now + timedelta(days=29))
    await store.add(BUSINESS, "acc", current, "budget-1", None, now)
    assert (
        await SqlGateFacts(sessions, BUSINESS, store).load("acc", now)
    ).operating_budget == 2_000


async def test_api_points_come_from_the_platform_and_unknown_stays_unknown(
    sessions: Sessions,
) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    periods = SqlPeriodStore(sessions)
    now = datetime.now(UTC)

    known = await SqlGateFacts(
        sessions, BUSINESS, periods, lambda account: {"acc": 120}.get(account)
    ).load("acc", now)
    unseen = await SqlGateFacts(sessions, BUSINESS, periods, lambda _account: None).load("acc", now)
    untracked = await SqlGateFacts(sessions, BUSINESS, periods).load("acc", now)

    assert known.api_points_rest == 120
    assert unseen.api_points_rest is None
    assert untracked.api_points_rest is None
