"""Метрики недели на настоящем PostgreSQL: изоляция по бизнесу, принятие предложений, тики."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.shadow_report import SqlShadowReport
from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.autopilot.adapters.ticks import SqlTicks
from tests.seed import seed_account

Sessions = async_sessionmaker[AsyncSession]
BUSINESS = uuid.uuid4()
OTHER = uuid.uuid4()
START = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
END = START + timedelta(days=7)
MID = START + timedelta(days=2)


async def change(
    sessions: Sessions,
    business: uuid.UUID,
    source: str,
    kind: str,
    ref: str,
    at: datetime,
    decision: str | None = None,
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO changes (business_id, account, source, decision_id, kind, target_ref,"
                " before, planned, status, explanation, idempotency_key, created_at)"
                " VALUES (:b, 'acc', :s, :d, :k, :r, '{}'::jsonb, '{}'::jsonb, 'applied', 'x',"
                " :key, :at)"
            ),
            {
                "b": business,
                "s": source,
                "d": decision,
                "k": kind,
                "r": ref,
                "at": at,
                "key": f"{source}-{kind}-{ref}-{at.isoformat()}-{uuid.uuid4()}",
            },
        )


def log(sessions: Sessions, business: uuid.UUID = BUSINESS) -> SqlDecisionLog:
    return SqlDecisionLog(sessions, business)


async def seed(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    await seed_account(sessions, OTHER, "acc")


async def test_acceptance_rollbacks_and_isolation(sessions: Sessions) -> None:
    await seed(sessions)
    inputs: dict[str, Any] = {}
    # Три предложения: принято правкой владельца, принято ответом на тест, отклонено.
    await log(sessions).record(
        "acc",
        "ad:1",
        "inefficient",
        "pause_ad",
        "awaiting_confirmation",
        inputs,
        "autopilot-2",
        MID,
    )
    await change(sessions, BUSINESS, "user", "pause_ad", "ad:1", MID + timedelta(hours=1))
    await log(sessions).record(
        "acc",
        "ad:2",
        "borderline",
        "start_test",
        "awaiting_confirmation",
        inputs,
        "autopilot-2",
        MID,
    )
    await log(sessions).record(
        "acc",
        "ad:2",
        "borderline",
        "start_test",
        "prepared",
        inputs,
        "autopilot-2",
        MID + timedelta(hours=2),
    )
    await log(sessions).record(
        "acc",
        "ad:3",
        "inefficient",
        "pause_ad",
        "awaiting_confirmation",
        inputs,
        "autopilot-2",
        MID,
    )
    # Чужой бизнес с такими же объявлениями в метрики не попадает.
    await log(sessions, OTHER).record(
        "acc",
        "ad:9",
        "inefficient",
        "pause_ad",
        "awaiting_confirmation",
        inputs,
        "autopilot-2",
        MID,
    )
    # Два самостоятельных действия, одно из них откатили.
    await change(sessions, BUSINESS, "autopilot", "pause_ad", "ad:5", MID)
    await change(sessions, BUSINESS, "autopilot", "pause_ad", "ad:6", MID)
    await change(
        sessions,
        BUSINESS,
        "rollback",
        "resume_ad",
        "ad:5",
        MID + timedelta(days=1),
        "outcome-rollback",
    )
    # Откат вне окна не считается.
    await change(
        sessions,
        BUSINESS,
        "rollback",
        "resume_ad",
        "ad:6",
        END + timedelta(days=1),
        "outcome-rollback",
    )

    metrics = await SqlShadowReport(sessions, BUSINESS).metrics(START, END)

    assert (metrics.proposals, metrics.accepted) == (3, 2)
    assert (metrics.auto_actions, metrics.auto_rollbacks) == (2, 1)
    other = await SqlShadowReport(sessions, OTHER).metrics(START, END)
    assert (other.proposals, other.accepted, other.auto_actions) == (1, 0, 0)


async def test_divergence_pairs_working_and_shadow_by_day(sessions: Sessions) -> None:
    await seed(sessions)
    inputs: dict[str, Any] = {}
    await log(sessions).record(
        "acc", "ad:1", "inefficient", "pause_ad", "applied", inputs, "autopilot-2", MID
    )
    await log(sessions).record(
        "acc", "shadow:ad:1", "inefficient", None, "shadow", inputs, "autopilot-3", MID
    )
    await log(sessions).record(
        "acc", "ad:2", "efficient", None, "no_action", inputs, "autopilot-2", MID
    )
    await log(sessions).record(
        "acc", "shadow:ad:2", "efficient", None, "shadow", inputs, "autopilot-3", MID
    )

    metrics = await SqlShadowReport(sessions, BUSINESS).metrics(START, END)

    assert (metrics.shadow_pairs, metrics.diverged) == (2, 1)


async def test_outcomes_and_reviewed_actions(sessions: Sessions) -> None:
    await seed(sessions)
    await log(sessions).record(
        "acc", "ad:1", "inefficient", "pause_ad", "planned", {"review": "ok"}, "autopilot-2", MID
    )
    await log(sessions).record(
        "acc", "ad:2", "inefficient", "pause_ad", "planned", {"review": "off"}, "autopilot-2", MID
    )
    async with sessions.begin() as session:
        for verdict, change_id in (("worse", 1), ("insufficient", 2), ("better", 3)):
            await session.execute(
                text(
                    "INSERT INTO decision_outcomes (business_id, account, change_id, object_ref,"
                    " kind, applied_at, evaluate_at, status, verdict, evaluated_at)"
                    " VALUES (:b, 'acc', :c, 'ad:1', 'pause_ad', :at, :at, 'evaluated', :v, :at)"
                ),
                {"b": BUSINESS, "c": change_id, "v": verdict, "at": MID},
            )

    metrics = await SqlShadowReport(sessions, BUSINESS).metrics(START, END)

    assert (metrics.judged, metrics.worse, metrics.insufficient) == (3, 1, 1)
    assert metrics.reviewed_actions == 1


async def test_missed_ticks_are_counted_from_the_first_tick(sessions: Sessions) -> None:
    ticks = SqlTicks(sessions)
    for hour in (0, 1, 2, 4, 5):  # тика в 3:00 нет
        await ticks.record(START + timedelta(hours=hour, minutes=20), 1)
    await ticks.record(START + timedelta(hours=5, minutes=50), 2)  # повтор часа не дублируется

    expected, missed = await ticks.missed(START, START + timedelta(hours=6))

    assert (expected, missed) == (6, 1)


async def test_no_ticks_yet_is_not_a_miss(sessions: Sessions) -> None:
    assert await SqlTicks(sessions).missed(START, END) == (0, 0)


async def _card(sessions: Sessions, ref: str, action: str, status: str, at: datetime) -> None:
    await log(sessions).record("acc", ref, "borderline", action, status, {}, "autopilot-2", at)


async def test_repeated_cards_for_one_ad_are_one_proposal(sessions: Sessions) -> None:
    await seed(sessions)
    for day in range(3):  # одну и ту же карточку цикл повторяет каждый день
        await _card(
            sessions, "ad:1", "pause_ad", "awaiting_confirmation", MID + timedelta(days=day)
        )
    await change(sessions, BUSINESS, "user", "pause_ad", "ad:1", MID + timedelta(days=1, hours=1))

    metrics = await SqlShadowReport(sessions, BUSINESS).metrics(START, END)

    assert (metrics.proposals, metrics.accepted) == (1, 1)


async def test_autonomous_test_start_is_not_counted_as_the_owner_agreeing(
    sessions: Sessions,
) -> None:
    await seed(sessions)
    await _card(sessions, "ad:2", "start_test", "awaiting_confirmation", MID)
    # Автопилот потом сам запустил тест: сначала «planned», потом «prepared».
    await _card(sessions, "ad:2", "start_test", "planned", MID + timedelta(days=1))
    await _card(sessions, "ad:2", "start_test", "prepared", MID + timedelta(days=1, minutes=1))

    metrics = await SqlShadowReport(sessions, BUSINESS).metrics(START, END)

    assert (metrics.proposals, metrics.accepted) == (1, 0)


async def test_late_answer_after_the_window_is_not_acceptance(sessions: Sessions) -> None:
    await seed(sessions)
    await _card(sessions, "ad:2", "start_test", "awaiting_confirmation", MID)
    await _card(sessions, "ad:2", "start_test", "prepared", MID + timedelta(days=8))

    metrics = await SqlShadowReport(sessions, BUSINESS).metrics(START, END + timedelta(days=9))

    assert (metrics.proposals, metrics.accepted) == (1, 0)


async def test_worker_standing_the_whole_window_counts_every_slot_as_missed(
    sessions: Sessions,
) -> None:
    ticks = SqlTicks(sessions)
    # Воркер работал до начала окна и встал: тики были раньше, в окне ни одного.
    for hour in range(0, 12):
        await ticks.record(START - timedelta(days=2) + timedelta(hours=hour, minutes=20), 1)

    expected, missed = await ticks.missed(START, END)

    assert (expected, missed) == (168, 168)
