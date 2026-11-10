"""Лестница доверия на настоящем PostgreSQL: срывы по журналу, открытие и откат ступени."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.settings import SqlAutopilotSettings
from adhelper.autopilot.adapters.trust import SqlTrustFacts
from adhelper.autopilot.application.trust import GrantDenied, grant, revoke
from adhelper.autopilot.domain.settings import DEFAULT_SETTINGS, AutopilotMode
from adhelper.autopilot.domain.trust import START_CLASSES, GrantRefusal, TrustClass
from adhelper.notifications.adapters.sql import SqlAlerts
from tests.seed import seed_account

Sessions = async_sessionmaker[AsyncSession]
BUSINESS = uuid.uuid4()
OTHER = uuid.uuid4()
NOW = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=8)


@pytest.fixture(autouse=True)
async def _owners(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    await seed_account(sessions, OTHER, "acc")


async def change(
    sessions: Sessions,
    business: uuid.UUID,
    source: str,
    status: str,
    decision: str | None,
    at: datetime,
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO changes (business_id, account, source, decision_id, kind, target_ref,"
                " before, planned, status, explanation, idempotency_key, created_at)"
                " VALUES (:b, 'acc', :s, :d, 'pause_ad', 'ad:1', '{}'::jsonb, '{}'::jsonb, :st,"
                " 'x', :key, :at)"
            ),
            {"b": business, "s": source, "d": decision, "st": status, "at": at,
             "key": str(uuid.uuid4())},
        )  # fmt: skip


async def auto_cabinet(sessions: Sessions, *, since: datetime = SINCE) -> SqlAutopilotSettings:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    await store.save(replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, auto_since=since), [])
    return store


async def test_trust_fields_round_trip_and_default_is_the_first_step(sessions: Sessions) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    await store.save(DEFAULT_SETTINGS, [])
    saved = await store.get()
    assert saved is not None and saved.granted == START_CLASSES
    assert saved.granted_at is None and saved.auto_since is None


async def test_clean_week_opens_the_next_step_and_writes_history(sessions: Sessions) -> None:
    store = await auto_cabinet(sessions)
    facts = SqlTrustFacts(sessions, BUSINESS)

    after = await grant(store, facts, TrustClass.SHIFT_LIMIT, NOW)

    assert TrustClass.SHIFT_LIMIT in after.granted and after.granted_at == NOW
    reread = await store.get()
    assert reread is not None and TrustClass.SHIFT_LIMIT in reread.granted
    assert any("Открыл" in line.text for line in await store.history())


async def test_rollback_after_the_start_blocks_the_step(sessions: Sessions) -> None:
    store = await auto_cabinet(sessions)
    await change(
        sessions, BUSINESS, "rollback", "applied", "outcome-rollback", SINCE + timedelta(days=2)
    )

    with pytest.raises(GrantDenied) as denied:
        await grant(store, SqlTrustFacts(sessions, BUSINESS), TrustClass.SHIFT_LIMIT, NOW)

    assert denied.value.refusal is GrantRefusal.HAD_FAILURES
    saved = await store.get()
    assert saved is not None and TrustClass.SHIFT_LIMIT not in saved.granted


@pytest.mark.parametrize("kind", ["failed", "rejected", "conflict"])
async def test_failed_autopilot_change_counts_as_a_failure(sessions: Sessions, kind: str) -> None:
    await change(sessions, BUSINESS, "autopilot", kind, None, SINCE + timedelta(days=1))
    assert await SqlTrustFacts(sessions, BUSINESS).failures_since(SINCE) == 1


async def test_budget_alert_and_demotion_count_and_other_business_does_not(
    sessions: Sessions,
) -> None:
    await SqlAlerts(sessions, BUSINESS).raise_alert("budget_stopped", {})
    store = await auto_cabinet(sessions, since=NOW - timedelta(days=1))
    await store.demote_from_auto("Выключил самостоятельный режим: тест")
    await change(
        sessions, OTHER, "rollback", "applied", "outcome-rollback", NOW - timedelta(days=1)
    )

    facts = SqlTrustFacts(sessions, BUSINESS)
    since = datetime.now(UTC) - timedelta(days=2)

    assert await facts.failures_since(since) == 2
    assert await SqlTrustFacts(sessions, OTHER).failures_since(since) == 1


async def test_too_early_and_wrong_order_do_not_change_anything(sessions: Sessions) -> None:
    store = await auto_cabinet(sessions, since=NOW - timedelta(days=3))
    facts = SqlTrustFacts(sessions, BUSINESS)
    with pytest.raises(GrantDenied) as early:
        await grant(store, facts, TrustClass.SHIFT_LIMIT, NOW)
    assert early.value.refusal is GrantRefusal.TOO_EARLY
    with pytest.raises(GrantDenied) as order:
        await grant(store, facts, TrustClass.SET_SCHEDULE, NOW)
    assert order.value.refusal is GrantRefusal.NOT_NEXT_STEP
    saved = await store.get()
    assert saved is not None and saved.granted == START_CLASSES


async def test_revoke_closes_the_step_and_everything_above_and_restarts_the_wait(
    sessions: Sessions,
) -> None:
    store = await auto_cabinet(sessions)
    facts = SqlTrustFacts(sessions, BUSINESS)
    await grant(store, facts, TrustClass.SHIFT_LIMIT, NOW)
    await grant(store, facts, TrustClass.SET_SCHEDULE, NOW + timedelta(days=8))

    after = await revoke(store, TrustClass.SHIFT_LIMIT, NOW + timedelta(days=9))

    assert after.granted == START_CLASSES and after.granted_at == NOW + timedelta(days=9)


async def test_first_step_classes_cannot_be_revoked(sessions: Sessions) -> None:
    store = await auto_cabinet(sessions)
    with pytest.raises(GrantDenied):
        await revoke(store, TrustClass.PAUSE_AD, NOW)


async def test_budget_alert_open_since_before_the_window_still_counts(sessions: Sessions) -> None:
    await SqlAlerts(sessions, BUSINESS).raise_alert("budget_stopped", {})
    later = datetime.now(UTC) + timedelta(hours=1)

    # Остановка бюджета началась до окна и не снята: кабинет в срыве и сейчас.
    assert await SqlTrustFacts(sessions, BUSINESS).failures_since(later) == 1
    await SqlAlerts(sessions, BUSINESS).resolve("budget_stopped")
    later = datetime.now(UTC) + timedelta(hours=1)
    assert await SqlTrustFacts(sessions, BUSINESS).failures_since(later) == 0


@pytest.mark.parametrize("status", ["failed", "rejected", "conflict"])
async def test_failed_auto_rollback_counts_as_a_failure(sessions: Sessions, status: str) -> None:
    await change(
        sessions, BUSINESS, "rollback", status, "outcome-rollback", SINCE + timedelta(days=1)
    )
    assert await SqlTrustFacts(sessions, BUSINESS).failures_since(SINCE) == 1


async def test_grant_needs_auto_mode_now(sessions: Sessions) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    # Вышел из «Сам» в «Предлагает», прошла неделя без единого самостоятельного действия.
    await store.save(replace(DEFAULT_SETTINGS, mode=AutopilotMode.CONFIRM, granted_at=SINCE), [])

    with pytest.raises(GrantDenied) as denied:
        await grant(store, SqlTrustFacts(sessions, BUSINESS), TrustClass.SHIFT_LIMIT, NOW)

    assert denied.value.refusal is GrantRefusal.NOT_AUTO
    saved = await store.get()
    assert saved is not None and TrustClass.SHIFT_LIMIT not in saved.granted


async def test_grant_refused_for_a_stopped_cabinet(sessions: Sessions) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    await store.save(
        replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, auto_since=SINCE, stopped_at=NOW),
        [],
    )

    with pytest.raises(GrantDenied) as denied:
        await grant(store, SqlTrustFacts(sessions, BUSINESS), TrustClass.SHIFT_LIMIT, NOW)

    assert denied.value.refusal is GrantRefusal.NOT_AUTO


async def test_wait_counts_from_the_later_of_last_grant_and_auto_start(
    sessions: Sessions,
) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    # Ступень закрыта давно, но в «Сам» кабинет вошёл только 3 дня назад: неделя ещё не прошла.
    await store.save(
        replace(
            DEFAULT_SETTINGS,
            mode=AutopilotMode.AUTO,
            granted_at=NOW - timedelta(days=30),
            auto_since=NOW - timedelta(days=3),
        ),
        [],
    )

    with pytest.raises(GrantDenied) as denied:
        await grant(store, SqlTrustFacts(sessions, BUSINESS), TrustClass.SHIFT_LIMIT, NOW)

    assert denied.value.refusal is GrantRefusal.TOO_EARLY


async def test_revoking_a_closed_class_changes_nothing_and_writes_nothing(
    sessions: Sessions,
) -> None:
    store = await auto_cabinet(sessions)
    before = await store.get()
    assert before is not None

    with pytest.raises(GrantDenied):
        await revoke(store, TrustClass.SHIFT_LIMIT, NOW)

    after = await store.get()
    assert after is not None and after.granted_at == before.granted_at
    assert not any("Закрыл" in line.text for line in await store.history())
