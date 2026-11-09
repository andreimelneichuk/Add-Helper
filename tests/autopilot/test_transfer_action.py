"""Перенос лимита между кампаниями как действие автопилота: политика → план → журнал."""

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.rules_file import load_policy_rules
from adhelper.autopilot.adapters.transfers import SqlTransferStore
from adhelper.autopilot.application.transfer import (
    AuthorizedTransfer,
    CampaignTraits,
    PolicyFacts,
    TransferOutcome,
    TransferResult,
    authorize_transfer,
    cancel_transfer,
    finish_open_transfers,
    restore_transfer,
    resume_transfer,
    run_transfer,
)
from adhelper.autopilot.domain.policy import PolicyAction, PolicyDecision, PolicyReason
from adhelper.autopilot.domain.transfers import TransferState
from adhelper.budget.adapters.ledgered import LedgeredBudgetPlatform, PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.guard import TransferStatus
from adhelper.budget.application.preflight import BudgetPreflight
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, LimitChange
from adhelper.budget.domain.transfer import TransferPlan
from adhelper.budget.domain.watch import BurnRate
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.rollback import RollbackStatus, rollback_change
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
BUDGET_RULES = replace(
    load_rules("budget-1"),
    max_increase_bp=1_000_000,
    min_increase_interval_days=0,
    change_spike_old_bp=0,
    change_spike_new_bp=0,
)
POLICY = load_policy_rules("policy-1")
PERIOD = BudgetPeriod(hard_limit=1_000_000, reserve_bp=1000)
ZERO = BurnRate.zero()
BUSINESS = uuid.uuid4()
MANUAL = {"a": CampaignTraits(False, False), "b": CampaignTraits(False, False)}
FACTS = PolicyFacts(
    allowed=frozenset({PolicyAction.INCREASE_LIMIT}),
    changes_today=0,
    moved_this_week=0,
    data_alert=False,
    api_points_rest=10_000,
)


def campaign(campaign_id: str, limit: int, spent: int = 0) -> CampaignBudget:
    return CampaignBudget(campaign_id, limit, spent, True, None)


async def authorize(
    platform: FakePlatform,
    amount: int,
    facts: PolicyFacts = FACTS,
    traits: Mapping[str, CampaignTraits] = MANUAL,
) -> TransferOutcome:
    return await authorize_transfer(
        platform, "acc", PERIOD, "a", "b", amount, facts, traits, NOW, POLICY
    )


async def test_policy_refusal_leaves_platform_untouched_and_names_reason() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)

    outcome = await authorize(platform, 50_000, replace(FACTS, data_alert=True))

    assert outcome.result is TransferResult.DENIED
    assert outcome.policy is not None
    assert outcome.policy.reason is PolicyReason.DATA_ALERT
    assert outcome.authorized is None
    assert platform.set_limit_calls == []


async def test_transfer_over_weekly_share_is_denied() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    cap = PERIOD.operating_budget * POLICY.max_weekly_move_bp // 10_000

    outcome = await authorize(platform, cap + 1)

    assert outcome.result is TransferResult.DENIED
    assert outcome.policy is not None
    assert outcome.policy.reason is PolicyReason.WEEKLY_SHARE
    assert platform.set_limit_calls == []


async def test_transfer_counts_as_two_changes_against_the_daily_limit() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    room_for_one = replace(FACTS, changes_today=POLICY.max_changes_per_day - 1)
    room_for_two = replace(FACTS, changes_today=POLICY.max_changes_per_day - 2)

    denied = await authorize(platform, 10_000, room_for_one)
    allowed = await authorize(platform, 10_000, room_for_two)

    assert denied.result is TransferResult.DENIED
    assert denied.policy is not None
    assert denied.policy.reason is PolicyReason.DAILY_LIMIT
    assert allowed.result is TransferResult.AUTHORIZED
    assert platform.set_limit_calls == []


async def test_recent_decrease_of_a_campaign_counts_as_a_limit_change() -> None:
    old = NOW - timedelta(days=60)
    platform = FakePlatform(
        [
            CampaignBudget("a", 500_000, 0, True, old),
            CampaignBudget("b", 100_000, 0, True, old),
        ],
        NOW,
    )
    auto = {"a": CampaignTraits(True, False), "b": CampaignTraits(True, False)}

    calm = await authorize(platform, 10_000, traits=auto)
    # Получатель недавно был донором и снижался: автостратегию не дёргаем.
    recent = replace(FACTS, last_limit_change={"b": NOW - timedelta(hours=1)})
    denied = await authorize(platform, 10_000, recent, auto)

    assert calm.result is TransferResult.AUTHORIZED
    assert denied.result is TransferResult.DENIED
    assert denied.policy is not None
    assert denied.policy.reason is PolicyReason.AUTO_STRATEGY_INTERVAL


async def test_unknown_traits_are_treated_as_learning_and_refused() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)

    outcome = await authorize(platform, 10_000, traits={})

    assert outcome.policy is not None
    assert outcome.policy.reason is PolicyReason.IN_LEARNING
    assert platform.set_limit_calls == []


async def test_unknown_campaign_and_plan_rejection_leave_platform_untouched() -> None:
    platform = FakePlatform([campaign("a", 500_000)], NOW)
    outcome = await authorize(platform, 10_000)
    assert outcome.result is TransferResult.UNKNOWN_CAMPAIGN
    # Донор уже потратил почти всё: лимит ниже расхода план не допускает.
    platform = FakePlatform([campaign("a", 500_000, spent=450_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 100_000)
    assert outcome.result is TransferResult.REJECTED
    assert platform.set_limit_calls == []


async def test_allowed_transfer_decreases_before_increasing_and_keeps_the_sum() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 50_000)
    assert outcome.result is TransferResult.AUTHORIZED
    assert outcome.authorized is not None
    assert outcome.authorized.decision.rules_version == "policy-1"

    status = await resume_transfer(
        platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )

    assert status is TransferStatus.DONE
    assert [c.campaign_id for c in platform.set_limit_calls] == ["a", "b"]
    assert max(platform.limit_sums) <= PERIOD.operating_budget


async def test_campaign_gone_mid_transfer_does_not_crash_and_keeps_money_safe() -> None:
    # Получатель исчез до первого шага: ничего не меняем, донор остаётся при своём лимите.
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    del platform.campaigns["b"]

    status = await resume_transfer(
        platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )

    assert status is TransferStatus.ROLLED_BACK
    assert platform.set_limit_calls == []
    assert platform.campaigns["a"].limit == 500_000

    # Донор исчез: сообщаем «застрял», человек увидит, исключения нет.
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    del platform.campaigns["a"]
    status = await resume_transfer(
        platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )
    assert status is TransferStatus.STUCK


async def test_campaign_gone_between_steps_is_compensated_not_crashed() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    # Донор уже снижен, а получатель пропал с площадки.
    platform.campaigns["a"] = campaign("a", 450_000)
    del platform.campaigns["b"]

    status = await resume_transfer(
        platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )

    assert status is TransferStatus.ROLLED_BACK
    assert platform.campaigns["a"].limit == 500_000


async def test_resume_after_break_finishes_without_repeating_the_donor_decrease() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    # Первый обрыв до применения, второй после применения снижения донора (ответ потерян).
    platform.faults.append(Fault("set_limit"))
    platform.faults.append(Fault("set_limit", after_apply=True))

    statuses = [
        await resume_transfer(
            platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
        )
        for _ in range(3)
    ]

    assert statuses == [
        TransferStatus.INTERRUPTED,
        TransferStatus.INTERRUPTED,
        TransferStatus.DONE,
    ]
    assert platform.campaigns["a"].limit == 450_000
    assert platform.campaigns["b"].limit == 150_000
    assert [c.campaign_id for c in platform.set_limit_calls].count("a") == 1
    assert max(platform.limit_sums) <= PERIOD.operating_budget


@settings(max_examples=150, deadline=None)
@given(
    st.integers(1, 400_000),
    st.integers(1, 100_000),
    st.integers(1, 100_000),
    st.lists(
        st.sampled_from(
            [
                Fault("set_limit"),
                Fault("set_limit", after_apply=True),
                Fault("read_campaigns"),
                Fault("fetch_spend"),
            ]
        ),
        max_size=6,
    ),
)
def test_sum_of_limits_never_exceeds_the_start_sum_even_with_faults(
    donor: int, recipient: int, amount: int, faults: list[Fault]
) -> None:
    async def scenario() -> None:
        platform = FakePlatform([campaign("a", donor), campaign("b", recipient)], NOW)
        start = platform.limit_sum
        outcome = await authorize(platform, amount)
        if outcome.authorized is None:
            assert platform.set_limit_calls == []
            return
        platform.faults = [replace(f) for f in faults]
        status = TransferStatus.INTERRUPTED
        for _ in range(len(faults) + 1):
            status = await resume_transfer(
                platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
            )
            if status is not TransferStatus.INTERRUPTED:
                break
        # Страж может отказать в росте получателя; сумма лимитов не выше начальной всегда.
        assert status in {
            TransferStatus.DONE,
            TransferStatus.REJECTED,
            TransferStatus.ROLLED_BACK,
            TransferStatus.STUCK,
        }
        assert max(platform.limit_sums) <= start
        if status is TransferStatus.DONE:
            assert platform.limit_sum == start

    asyncio.run(scenario())


Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def _owner(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


async def test_journaled_transfer_is_rolled_back_by_new_records(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledger = Ledger(
        repository=SqlChangeRepository(sessions, BUSINESS),
        gateway=PlatformChangeGateway(platform),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=BudgetPreflight(platform, PERIOD, ZERO, lambda: platform.now, BUDGET_RULES),
    )
    ledgered = LedgeredBudgetPlatform(platform, ledger, ChangeSource.AUTOPILOT)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None

    status = await resume_transfer(
        ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )
    assert status is TransferStatus.DONE
    repository = SqlChangeRepository(sessions, BUSINESS)
    rows = await repository.all()
    assert [(r.kind, r.source, r.status) for r in rows] == [
        (ChangeKind.SET_LIMIT, ChangeSource.AUTOPILOT, ChangeStatus.APPLIED)
    ] * 2

    # Отмена идёт в обратном порядке: сначала получатель, потом донор; записи только добавляются.
    result_recipient = await rollback_change(ledger, rows[1].id)
    result_donor = await rollback_change(ledger, rows[0].id)

    assert result_recipient.status is RollbackStatus.APPLIED
    assert result_donor.status is RollbackStatus.APPLIED
    after = await repository.all()
    assert len(after) == 4
    assert [r.source for r in after[2:]] == [ChangeSource.ROLLBACK] * 2
    assert platform.campaigns["a"].limit == 500_000
    assert platform.campaigns["b"].limit == 100_000
    assert max(platform.limit_sums) <= PERIOD.operating_budget


def outage_after_first_limit(platform: FakePlatform) -> None:
    """Донор снижен, затем площадка пропадает: перенос остаётся недоведённым."""
    original = platform.set_limit

    async def once(account: str, change: LimitChange) -> None:
        await original(account, change)
        platform.unavailable = True
        platform.set_limit = original  # type: ignore[method-assign]

    platform.set_limit = once  # type: ignore[method-assign]


def ledger_for(platform: FakePlatform, sessions: Sessions, engine: AsyncEngine) -> Ledger:
    return Ledger(
        repository=SqlChangeRepository(sessions, BUSINESS),
        gateway=PlatformChangeGateway(platform),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=BudgetPreflight(platform, PERIOD, ZERO, lambda: platform.now, BUDGET_RULES),
    )


def test_authorized_transfer_cannot_be_built_outside_the_policy() -> None:
    plan = TransferPlan("a", "b", 450_000, 150_000, 500_000)
    allowed = PolicyDecision(None, POLICY.version, {})

    with pytest.raises(TypeError):
        AuthorizedTransfer(plan, allowed, 50_000)
    with pytest.raises(TypeError):
        AuthorizedTransfer(plan, allowed, 50_000, object())


async def test_resume_refuses_a_stale_or_denied_decision() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    other = replace(POLICY, version="policy-0")

    status = await resume_transfer(
        platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, other
    )

    assert status is TransferStatus.REJECTED
    assert platform.set_limit_calls == []


def test_only_the_transfer_module_drives_transfers() -> None:
    from pathlib import Path

    root = Path(__file__).parents[2] / "src"
    users = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "drive_transfer" in path.read_text()
    }
    assert users == {
        "adhelper/autopilot/application/transfer.py",
        "adhelper/budget/application/guard.py",
    }


async def test_run_transfer_writes_intent_first_and_tags_journal_with_transfer_id(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledger = ledger_for(platform, sessions, engine)
    ledgered = LedgeredBudgetPlatform(platform, ledger, ChangeSource.AUTOPILOT)
    store = SqlTransferStore(sessions, BUSINESS)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None

    transfer_id, status = await run_transfer(
        store, ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )

    assert status is TransferStatus.DONE
    record = await store.get(transfer_id)
    assert record is not None and record.state is TransferState.DONE
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert {r.decision_id for r in rows} == {f"transfer:{transfer_id}"}
    assert await store.open_transfers() == []


async def test_other_business_does_not_see_the_transfer(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledgered = LedgeredBudgetPlatform(
        platform, ledger_for(platform, sessions, engine), ChangeSource.AUTOPILOT
    )
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    transfer_id, _ = await run_transfer(
        SqlTransferStore(sessions, BUSINESS),
        ledgered,
        "acc",
        PERIOD,
        outcome.authorized,
        ZERO,
        NOW,
        BUDGET_RULES,
        POLICY,
    )

    assert await SqlTransferStore(sessions, uuid.uuid4()).get(transfer_id) is None


async def test_open_transfer_is_finished_after_a_crash(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledgered = LedgeredBudgetPlatform(
        platform, ledger_for(platform, sessions, engine), ChangeSource.AUTOPILOT
    )
    store = SqlTransferStore(sessions, BUSINESS)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    # Донор снижен, ответ на рост получателя потерян: перенос остаётся открытым в базе.
    outage_after_first_limit(platform)
    _, first = await run_transfer(
        store, ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )
    assert first is TransferStatus.INTERRUPTED
    assert len(await store.open_transfers()) == 1
    platform.unavailable = False

    done = await finish_open_transfers(store, ledgered, PERIOD, ZERO, NOW, BUDGET_RULES, POLICY)

    assert set(done.values()) == {TransferStatus.DONE}
    assert platform.campaigns["a"].limit == 450_000
    assert platform.campaigns["b"].limit == 150_000
    assert await store.open_transfers() == []


async def test_open_transfer_is_claimed_by_one_worker_at_a_time(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledgered = LedgeredBudgetPlatform(
        platform, ledger_for(platform, sessions, engine), ChangeSource.AUTOPILOT
    )
    store = SqlTransferStore(sessions, BUSINESS)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    outage_after_first_limit(platform)
    transfer_id, _ = await run_transfer(
        store, ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )
    platform.unavailable = False

    first = await store.claim_open(timedelta(minutes=10))
    second = await store.claim_open(timedelta(minutes=10))
    expired = await store.claim_open(timedelta(0))

    assert [r.id for r in first] == [transfer_id]
    assert second == []
    assert [r.id for r in expired] == [transfer_id]


async def test_two_finishers_do_not_both_drive_the_same_transfer(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledgered = LedgeredBudgetPlatform(
        platform, ledger_for(platform, sessions, engine), ChangeSource.AUTOPILOT
    )
    store = SqlTransferStore(sessions, BUSINESS)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    outage_after_first_limit(platform)
    await run_transfer(
        store, ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )
    platform.unavailable = False
    platform.set_limit_calls.clear()

    results = await asyncio.gather(
        finish_open_transfers(store, ledgered, PERIOD, ZERO, NOW, BUDGET_RULES, POLICY),
        finish_open_transfers(store, ledgered, PERIOD, ZERO, NOW, BUDGET_RULES, POLICY),
    )

    assert sorted(len(r) for r in results) == [0, 1]
    assert [c.campaign_id for c in platform.set_limit_calls] == ["b"]


async def test_finished_state_is_not_overwritten_by_a_late_writer(
    sessions: Sessions, _owner: None
) -> None:
    from adhelper.autopilot.domain.transfers import TransferRecord

    store = SqlTransferStore(sessions, BUSINESS)
    record = TransferRecord(
        str(uuid.uuid4()), "acc", "a", "b", 500_000, 450_000, 150_000, 50_000,
        {"reason": None, "rules_version": "policy-1", "inputs": {}},
        state=TransferState.RUNNING,
    )  # fmt: skip
    await store.add(record)

    assert await store.set_state(record.id, TransferState.DONE) is True
    assert await store.set_state(record.id, TransferState.STUCK) is False
    done = await store.get(record.id)
    assert done is not None and done.state is TransferState.DONE


async def test_corrupted_open_record_is_marked_stuck_not_driven(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    from adhelper.autopilot.domain.transfers import TransferRecord

    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledgered = LedgeredBudgetPlatform(
        platform, ledger_for(platform, sessions, engine), ChangeSource.AUTOPILOT
    )
    store = SqlTransferStore(sessions, BUSINESS)
    # Цель донора не равна «старт минус сумма»: такую запись нельзя исполнять.
    bad = TransferRecord(
        str(uuid.uuid4()), "acc", "a", "b", 500_000, 100_000, 150_000, 50_000,
        {"reason": None, "rules_version": "policy-1", "inputs": {}},
    )  # fmt: skip
    await store.add(bad)

    result = await finish_open_transfers(store, ledgered, PERIOD, ZERO, NOW, BUDGET_RULES, POLICY)

    assert result == {bad.id: TransferStatus.STUCK}
    assert platform.set_limit_calls == []
    stored = await store.get(bad.id)
    assert stored is not None and stored.state is TransferState.STUCK


def test_restore_refuses_an_inconsistent_record() -> None:
    from adhelper.autopilot.domain.transfers import TransferRecord

    decision: dict[str, Any] = {"reason": None, "rules_version": "policy-1", "inputs": {}}
    for donor_target, recipient_target, amount in (
        (100_000, 150_000, 50_000),  # снижение не равно сумме
        (450_000, 20_000, 50_000),  # лимит получателя до переноса был бы отрицательным
        (500_000, 100_000, 0),  # пустой перенос
    ):
        record = TransferRecord(
            "x", "acc", "a", "b", 500_000, donor_target, recipient_target, amount, decision
        )
        with pytest.raises(ValueError):
            restore_transfer(record)


async def test_stale_open_transfer_is_cancelled_by_reverse_transfer(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledgered = LedgeredBudgetPlatform(
        platform, ledger_for(platform, sessions, engine), ChangeSource.AUTOPILOT
    )
    store = SqlTransferStore(sessions, BUSINESS)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    outage_after_first_limit(platform)
    transfer_id, first = await run_transfer(
        store, ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )
    assert first is TransferStatus.INTERRUPTED
    assert platform.campaigns["a"].limit == 450_000
    platform.unavailable = False

    other = replace(POLICY, version="policy-0")
    result = await finish_open_transfers(store, ledgered, PERIOD, ZERO, NOW, BUDGET_RULES, other)

    assert result == {transfer_id: TransferStatus.DONE}
    record = await store.get(transfer_id)
    assert record is not None and record.state is TransferState.CANCELLED
    assert platform.campaigns["a"].limit == 500_000
    assert platform.campaigns["b"].limit == 100_000


async def test_cancel_transfer_returns_recipient_first_then_donor(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    ledgered = LedgeredBudgetPlatform(
        platform, ledger_for(platform, sessions, engine), ChangeSource.AUTOPILOT
    )
    store = SqlTransferStore(sessions, BUSINESS)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    transfer_id, _ = await run_transfer(
        store, ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES, POLICY
    )
    platform.set_limit_calls.clear()
    platform.limit_sums.clear()

    status = await cancel_transfer(store, ledgered, transfer_id, PERIOD, ZERO, NOW, BUDGET_RULES)

    assert status is TransferStatus.DONE
    assert [c.campaign_id for c in platform.set_limit_calls] == ["b", "a"]
    assert platform.campaigns["a"].limit == 500_000
    assert platform.campaigns["b"].limit == 100_000
    assert max(platform.limit_sums) <= PERIOD.operating_budget
    record = await store.get(transfer_id)
    assert record is not None and record.state is TransferState.CANCELLED
    assert (
        await cancel_transfer(store, ledgered, transfer_id, PERIOD, ZERO, NOW, BUDGET_RULES) is None
    )


def test_restored_transfer_keeps_the_stored_decision() -> None:
    from adhelper.autopilot.domain.transfers import TransferRecord

    record = TransferRecord(
        "x", "acc", "a", "b", 500_000, 450_000, 150_000, 50_000,
        {"reason": None, "rules_version": "policy-1", "inputs": {}},
    )  # fmt: skip

    restored = restore_transfer(record)

    assert restored.plan.donor_start == 500_000
    assert restored.decision.rules_version == "policy-1"
