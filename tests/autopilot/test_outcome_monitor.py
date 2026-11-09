"""Мониторинг исходов на настоящем журнале и PostgreSQL: оценка, автооткат, предохранители."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.ledgered import LedgeredActions
from adhelper.autopilot.adapters.outcomes import SqlOutcomeStore
from adhelper.autopilot.adapters.rules_file import load_outcome_rules
from adhelper.autopilot.application.outcomes import OutcomeMonitor
from adhelper.autopilot.domain.outcomes import OutcomeVerdict
from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.domain.calendar import moscow_period, platform_date
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource
from adhelper.notifications.adapters.sql import SqlAlerts
from adhelper.platforms.adapters.gateway import RoutingGateway
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
# Журнал не даёт менять created_at, поэтому «сейчас» сдвигаем вперёд от настоящего времени записи.
APPLIED = datetime.now(UTC)
NOW = APPLIED + timedelta(days=12)
RULES = load_outcome_rules("outcomes-1")
RUB = 1_000_000
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
async def _owners(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO campaigns (business_id, account, campaign_id, name, state,"
                " limit_micros, targeting) VALUES (:b, 'acc', 'c1', 'К', 'on', 10, '{}'::jsonb)"
            ),
            {"b": BUSINESS},
        )
        for ad in ("bad", "good"):
            await session.execute(
                text(
                    "INSERT INTO ads (business_id, account, ad_id, campaign_id, title, text,"
                    " href, state) VALUES (:b, 'acc', :a, 'c1', 'Т', 'Т', 'https://x.example',"
                    " 'on')"
                ),
                {"b": BUSINESS, "a": ad},
            )


async def put_stats(
    sessions: Sessions, ad: str, first: datetime, days: int, clicks: int, cost_rub: int, leads: int
) -> None:
    async with sessions.begin() as session:
        for back in range(days):
            await session.execute(
                text(
                    "INSERT INTO ad_stats (business_id, account, ad_id, day, impressions, clicks,"
                    " cost_micros, conversions) VALUES (:b, 'acc', :a, :d, 1000, :c, :s, :l)"
                ),
                {
                    "b": BUSINESS,
                    "a": ad,
                    "d": platform_date(first + timedelta(days=back)),
                    "c": clicks,
                    "s": cost_rub * RUB,
                    "l": leads,
                },
            )


async def history(sessions: Sessions, after_leads_per_day: int) -> None:
    """До паузы: оба объявления по 3 обращения в день; после: остаётся одно «good»."""
    before = APPLIED - timedelta(days=6)  # окно «до» = окно «после», сдвинутое на неделю
    after = APPLIED + timedelta(days=1)
    for ad in ("bad", "good"):
        await put_stats(sessions, ad, before, 7, 20, 600, 3)
    await put_stats(sessions, "good", after, 7, 40, 1200, after_leads_per_day)


class Rig:
    def __init__(
        self,
        sessions: Sessions,
        engine: AsyncEngine,
        now: datetime = NOW,
        store: SqlOutcomeStore | None = None,
        demoted: bool = True,
    ) -> None:
        self.now = now
        self.platform = FakePlatform([], NOW)
        self.platform.ads = {"ad:bad": True, "ad:good": True}
        self.ledger = Ledger(
            SqlChangeRepository(sessions, BUSINESS),
            RoutingGateway(PlatformChangeGateway(self.platform), self.platform),
            PostgresAccountLock(engine),
            3,
        )
        self.sessions = sessions
        self.demotions: list[str] = []

        async def demote(reason: str) -> bool:
            self.demotions.append(reason)
            return demoted

        self.monitor = OutcomeMonitor(
            store or SqlOutcomeStore(sessions, BUSINESS),
            self.ledger,
            SqlAlerts(sessions, BUSINESS),
            RULES,
            lambda: self.now,
            demote,
        )

    async def pause_bad(self) -> int:
        async def no_increase(account: str, campaign: str, limit: int) -> str:
            return "rejected"

        actions = LedgeredActions(self.ledger, no_increase)
        await actions.pause_ad("acc", "ad:bad", "Остановили", 1)
        return (await SqlChangeRepository(self.sessions, BUSINESS).all())[0].id


async def journal_rollbacks(sessions: Sessions, count: int, *, ago: int = 5) -> None:
    """Прошлые автоотмены в журнале: остановка автопилота и откат к ней.

    Сами остановки старше окна учёта, на оценку они не попадают.
    """
    async with sessions.begin() as session:
        for number in range(count):
            original = await session.scalar(
                text(
                    "INSERT INTO changes (business_id, account, source, kind, target_ref, before,"
                    " planned, status, explanation, idempotency_key, created_at)"
                    " VALUES (:b, 'acc', 'autopilot', 'pause_ad', 'ad:x', '{}'::jsonb,"
                    " '{}'::jsonb, 'applied', 'x', :k, :old) RETURNING id"
                ),
                {"b": BUSINESS, "k": f"old-{number}", "old": NOW - timedelta(days=40)},
            )
            await session.execute(
                text(
                    "INSERT INTO changes (business_id, account, source, kind, target_ref, before,"
                    " planned, status, explanation, idempotency_key, created_at, reverts_change_id)"
                    " VALUES (:b, 'acc', 'rollback', 'resume_ad', 'ad:x', '{}'::jsonb,"
                    " '{}'::jsonb, 'applied', 'x', :k, :at, :orig)"
                ),
                {
                    "b": BUSINESS,
                    "k": f"rollback:old-{number}",
                    "at": NOW - timedelta(days=ago),
                    "orig": original,
                },
            )


async def test_real_worsening_rolls_back_through_the_journal(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    change_id = await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)  # 7 обращений против 42: цена выросла в 6 раз

    result = await rig.monitor.run("acc")

    assert result.rolled_back == 1 and result.verdicts == [OutcomeVerdict.WORSE]
    assert rig.platform.ads["ad:bad"] is True
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    rollback = next(row for row in rows if row.reverts_change_id == change_id)
    assert rollback.source is ChangeSource.ROLLBACK and rollback.kind is ChangeKind.RESUME_AD
    assert rollback.before == {"active": False} and rollback.after == {"active": True}
    assert "выросла на" in rollback.explanation and "₽" in rollback.explanation
    async with sessions() as session:
        row = (
            await session.execute(
                text("SELECT verdict, rollback_change_id, status FROM decision_outcomes")
            )
        ).one()
    assert (row.verdict, row.rollback_change_id, row.status) == ("worse", rollback.id, "evaluated")


async def test_noise_does_not_trigger_rollback(sessions: Sessions, engine: AsyncEngine) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=5)  # 35 против 42: в пределах шума

    result = await rig.monitor.run("acc")

    assert result.rolled_back == 0 and result.verdicts == [OutcomeVerdict.SAME]
    assert rig.platform.ads["ad:bad"] is False


async def test_not_due_yet_changes_nothing(sessions: Sessions, engine: AsyncEngine) -> None:
    rig = Rig(sessions, engine, APPLIED + timedelta(days=3))
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)

    result = await rig.monitor.run("acc")

    assert result.registered == 1 and result.evaluated == 0
    assert rig.platform.ads["ad:bad"] is False


async def test_other_change_in_window_blocks_the_verdict(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO changes (business_id, account, source, kind, target_ref, before,"
                " planned, status, explanation, idempotency_key, created_at)"
                " VALUES (:b, 'acc', 'user', 'set_limit', 'c1', '{}'::jsonb, '{}'::jsonb,"
                " 'applied', 'x', 'k1', :at)"
            ),
            {"b": BUSINESS, "at": APPLIED + timedelta(days=2)},
        )

    result = await rig.monitor.run("acc")

    assert result.verdicts == [OutcomeVerdict.INSUFFICIENT] and result.rolled_back == 0
    assert rig.platform.ads["ad:bad"] is False


async def test_untrusted_data_freezes_evaluation(sessions: Sessions, engine: AsyncEngine) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    await SqlAlerts(sessions, BUSINESS).raise_alert("data_untrusted", {})

    result = await rig.monitor.run("acc")

    assert result.frozen and result.evaluated == 0 and result.rolled_back == 0
    assert rig.platform.ads["ad:bad"] is False
    async with sessions() as session:
        count = await session.scalar(text("SELECT count(*) FROM decision_outcomes"))
    assert count == 0


async def test_rollback_limit_stops_rollbacks_and_demotes(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    # Два автоотката за последние две недели уже были.
    await journal_rollbacks(sessions, 2)

    result = await rig.monitor.run("acc")

    assert result.rolled_back == 0
    assert result.demoted_reason is not None and rig.demotions == [result.demoted_reason]
    assert rig.platform.ads["ad:bad"] is False


async def test_second_run_does_not_repeat_the_rollback(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)

    await rig.monitor.run("acc")
    again = await rig.monitor.run("acc")

    assert again.evaluated == 0 and again.rolled_back == 0
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert sum(row.source is ChangeSource.ROLLBACK for row in rows) == 1


async def test_owner_reverted_change_is_skipped(sessions: Sessions, engine: AsyncEngine) -> None:
    from adhelper.ledger.application.rollback import rollback_change

    rig = Rig(sessions, engine)
    change_id = await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    await rollback_change(rig.ledger, change_id)

    result = await rig.monitor.run("acc")

    assert result.evaluated == 0 and result.rolled_back == 0
    async with sessions() as session:
        status = await session.scalar(text("SELECT status FROM decision_outcomes"))
    assert status == "skipped"


async def test_other_business_is_not_touched(sessions: Sessions, engine: AsyncEngine) -> None:
    other = uuid.uuid4()
    await seed_account(sessions, other, "acc")
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)

    other_store = SqlOutcomeStore(sessions, other)
    assert await other_store.register_new("acc", RULES, NOW) == 0
    assert list(await other_store.claim_due("acc", NOW)) == []


async def test_digest_counts_and_is_sent_once_a_day(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    await rig.monitor.run("acc")

    assert await rig.monitor.digest("acc", always=False) is True
    assert await rig.monitor.digest("acc", always=False) is False
    async with sessions() as session:
        details = await session.scalar(
            text("SELECT details FROM alerts WHERE kind = 'autopilot_digest'")
        )
    assert details["worse"] == 1 and details["rolled_back"] == 1


async def test_second_rollback_is_done_and_returns_autopilot_to_confirm(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    await journal_rollbacks(sessions, 1)

    result = await rig.monitor.run("acc")

    assert result.rolled_back == 1 and rig.platform.ads["ad:bad"] is True
    assert len(rig.demotions) == 1
    async with sessions() as session:
        kinds = set(await session.scalars(text("SELECT kind FROM alerts")))
    assert {"autopilot_demoted", "autopilot_rolled_back"} <= kinds


async def outcome_row(sessions: Sessions) -> Any:
    async with sessions() as session:
        return (await session.execute(text("SELECT * FROM decision_outcomes"))).one()


class FlakySave(SqlOutcomeStore):
    """Воркер падает сразу после отката, до записи исхода."""

    failures = 1

    async def save(self, outcome_id: int, **fields: Any) -> bool:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("упали после отката")
        return await super().save(outcome_id, **fields)


async def test_rollbacks_are_counted_in_the_journal_not_in_outcome_rows(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    # Строки исходов с откатом без записи в журнале счётчик не двигают.
    async with sessions.begin() as session:
        for number in (101, 102):
            await session.execute(
                text(
                    "INSERT INTO decision_outcomes (business_id, account, change_id, object_ref,"
                    " kind, applied_at, evaluate_at, status, verdict, rollback_change_id,"
                    " evaluated_at) VALUES (:b, 'acc', :n, 'ad:x', 'pause_ad', :at, :at,"
                    " 'evaluated', 'same', :n, :now)"
                ),
                {"b": BUSINESS, "n": number, "at": APPLIED, "now": NOW - timedelta(days=5)},
            )

    result = await rig.monitor.run("acc")

    assert result.rolled_back == 1 and rig.demotions == []


async def test_crash_between_rollback_and_save_loses_nothing(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine, store=FlakySave(sessions, BUSINESS))
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)

    with pytest.raises(RuntimeError):
        await rig.monitor.run("acc")
    rig.now = NOW + timedelta(hours=2)  # аренда захвата истекла
    await rig.monitor.run("acc")

    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    rollbacks = [row for row in rows if row.source is ChangeSource.ROLLBACK]
    assert len(rollbacks) == 1 and rig.platform.ads["ad:bad"] is True
    row = await outcome_row(sessions)
    assert (row.status, row.verdict, row.rollback_change_id) == (
        "evaluated",
        "worse",
        rollbacks[0].id,
    )
    async with sessions() as session:
        sent = await session.scalar(
            text("SELECT count(*) FROM alerts WHERE kind = 'autopilot_rolled_back'")
        )
    assert sent == 1


async def test_pending_rollback_keeps_the_outcome_pending_and_is_retried(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    rig.platform.faults.append(Fault("set_ad_active"))  # площадка не ответила на откат

    first = await rig.monitor.run("acc")

    assert first.rolled_back == 0 and rig.platform.ads["ad:bad"] is False
    assert (await outcome_row(sessions)).status == "pending"

    rig.now = NOW + timedelta(hours=1)
    await rig.monitor.run("acc")

    assert rig.platform.ads["ad:bad"] is True
    row = await outcome_row(sessions)
    assert (row.status, row.verdict) == ("evaluated", "worse") and row.rollback_change_id
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert sum(r.source is ChangeSource.ROLLBACK for r in rows) == 1
    async with sessions() as session:
        sent = await session.scalar(
            text("SELECT count(*) FROM alerts WHERE kind = 'autopilot_rolled_back'")
        )
    assert sent == 1


async def test_two_workers_never_take_the_same_outcome(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    first, second = SqlOutcomeStore(sessions, BUSINESS), SqlOutcomeStore(sessions, BUSINESS)
    await first.register_new("acc", RULES, NOW)

    taken = await asyncio.gather(first.claim_due("acc", NOW), second.claim_due("acc", NOW))

    assert sorted(len(part) for part in taken) == [0, 1]
    # Пока аренда не истекла, захваченное не берут; истекла — берут снова.
    assert list(await second.claim_due("acc", NOW + timedelta(minutes=5))) == []
    assert len(await second.claim_due("acc", NOW + timedelta(hours=2))) == 1


async def test_save_writes_only_a_claimed_outcome(sessions: Sessions, engine: AsyncEngine) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    store = SqlOutcomeStore(sessions, BUSINESS)
    await store.register_new("acc", RULES, NOW)
    outcome_id = (await outcome_row(sessions)).id
    fields: dict[str, Any] = {
        "status": "evaluated",
        "verdict": OutcomeVerdict.SAME,
        "cpl_change_bp": 0,
        "noise_bp": 0,
        "measured": None,
        "rollback_change_id": None,
        "note": "x",
        "rules_version": "outcomes-1",
        "at": NOW,
    }

    assert await store.save(outcome_id, **fields) is False
    assert (await outcome_row(sessions)).status == "pending"
    await store.claim_due("acc", NOW)
    assert await store.save(outcome_id, **fields) is True
    assert (await outcome_row(sessions)).status == "evaluated"


async def test_missing_days_in_the_window_wait_instead_of_judging(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    before = APPLIED - timedelta(days=6)
    for ad in ("bad", "good"):
        await put_stats(sessions, ad, before, 7, 20, 600, 3)
    # Статистика догружена только за 5 дней из 7: обращений мало не потому, что стало хуже.
    await put_stats(sessions, "good", APPLIED + timedelta(days=1), 5, 40, 1200, 1)

    waiting = await rig.monitor.run("acc")

    assert waiting.evaluated == 0 and waiting.rolled_back == 0
    assert rig.platform.ads["ad:bad"] is False
    assert (await outcome_row(sessions)).status == "pending"

    rig.now = APPLIED + timedelta(days=20)  # ждать дальше нельзя: «не хватает данных», без отката
    final = await rig.monitor.run("acc")

    assert final.rolled_back == 0 and final.verdicts == [OutcomeVerdict.INSUFFICIENT]
    assert rig.platform.ads["ad:bad"] is False
    assert (await outcome_row(sessions)).status == "evaluated"


async def test_days_not_yet_matured_are_not_complete(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    store = SqlOutcomeStore(sessions, BUSINESS)
    await store.register_new("acc", RULES, NOW)
    (pending,) = await store.claim_due("acc", NOW)

    mature = await store.measure("acc", pending, RULES, NOW)
    early = await store.measure("acc", pending, RULES, APPLIED + timedelta(days=8))

    assert mature.complete is True
    assert early.complete is False and early.after.leads < mature.after.leads


async def test_old_stops_are_not_taken_into_the_account_in_a_batch(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    async with sessions.begin() as session:
        for number in range(3):
            await session.execute(
                text(
                    "INSERT INTO changes (business_id, account, source, kind, target_ref, before,"
                    " planned, status, explanation, idempotency_key, created_at)"
                    " VALUES (:b, 'acc', 'autopilot', 'pause_ad', 'ad:bad', '{}'::jsonb,"
                    " '{}'::jsonb, 'applied', 'x', :k, :at)"
                ),
                {"b": BUSINESS, "k": f"ancient-{number}", "at": NOW - timedelta(days=30)},
            )

    result = await rig.monitor.run("acc")

    assert result.registered == 0 and result.evaluated == 0
    async with sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM decision_outcomes")) == 0


async def test_zero_leads_rollback_says_what_happened(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    change_id = await rig.pause_bad()
    await history(sessions, after_leads_per_day=0)

    result = await rig.monitor.run("acc")

    assert result.rolled_back == 1
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    rollback = next(row for row in rows if row.reverts_change_id == change_id)
    assert "обращений 0" in rollback.explanation and "8 400 ₽" in rollback.explanation
    assert "200 ₽" in rollback.explanation and "выросла" not in rollback.explanation


async def test_change_before_the_action_in_the_window_blocks_the_verdict(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    await insert_user_change(sessions, "early", APPLIED - timedelta(days=3))

    result = await rig.monitor.run("acc")

    assert result.verdicts == [OutcomeVerdict.INSUFFICIENT] and result.rolled_back == 0


async def test_change_on_the_last_day_of_the_window_blocks_the_verdict(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    # Последняя минута последнего дня окна «после» по Москве: окно статистики и вмешательств одно.
    _, end = moscow_period(platform_date(APPLIED) + timedelta(days=7), 1)
    await insert_user_change(sessions, "late", end - timedelta(minutes=1))

    result = await rig.monitor.run("acc")

    assert result.verdicts == [OutcomeVerdict.INSUFFICIENT] and result.rolled_back == 0


async def test_evaluation_time_counts_from_the_end_of_the_window_in_moscow(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine)
    await rig.pause_bad()
    store = SqlOutcomeStore(sessions, BUSINESS)
    await store.register_new("acc", RULES, NOW)

    row = await outcome_row(sessions)

    ready_day = platform_date(row.applied_at) + timedelta(
        days=RULES.window_days + RULES.maturity_days
    )
    assert row.evaluate_at == moscow_period(ready_day, 1)[0]


async def test_demote_notice_is_sent_even_if_the_mode_was_already_changed(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    rig = Rig(sessions, engine, demoted=False)
    await rig.pause_bad()
    await history(sessions, after_leads_per_day=1)
    await journal_rollbacks(sessions, 1)

    await rig.monitor.run("acc")

    async with sessions() as session:
        kinds = list(await session.scalars(text("SELECT kind FROM alerts")))
    assert kinds.count("autopilot_demoted") == 1


async def insert_user_change(sessions: Sessions, key: str, at: datetime) -> None:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO changes (business_id, account, source, kind, target_ref, before,"
                " planned, status, explanation, idempotency_key, created_at)"
                " VALUES (:b, 'acc', 'user', 'set_limit', 'c1', '{}'::jsonb, '{}'::jsonb,"
                " 'applied', 'x', :k, :at)"
            ),
            {"b": BUSINESS, "k": key, "at": at},
        )
