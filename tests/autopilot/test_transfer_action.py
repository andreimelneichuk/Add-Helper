"""Перенос лимита между кампаниями как действие автопилота: политика → план → журнал."""

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.rules_file import load_policy_rules
from adhelper.autopilot.application.transfer import (
    CampaignTraits,
    PolicyFacts,
    TransferOutcome,
    TransferResult,
    authorize_transfer,
    resume_transfer,
)
from adhelper.autopilot.domain.policy import PolicyAction, PolicyReason
from adhelper.budget.adapters.ledgered import LedgeredBudgetPlatform, PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.guard import TransferStatus
from adhelper.budget.application.preflight import BudgetPreflight
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget
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
        platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES
    )

    assert status is TransferStatus.DONE
    assert [c.campaign_id for c in platform.set_limit_calls] == ["a", "b"]
    assert max(platform.limit_sums) <= PERIOD.operating_budget


async def test_resume_after_break_finishes_without_repeating_the_donor_decrease() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    outcome = await authorize(platform, 50_000)
    assert outcome.authorized is not None
    # Первый обрыв до применения, второй после применения снижения донора (ответ потерян).
    platform.faults.append(Fault("set_limit"))
    platform.faults.append(Fault("set_limit", after_apply=True))

    statuses = [
        await resume_transfer(platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES)
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
                platform, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES
            )
            if status is not TransferStatus.INTERRUPTED:
                break
        # Страж может отказать в росте получателя; сумма лимитов не выше начальной всегда.
        assert status in {TransferStatus.DONE, TransferStatus.REJECTED}
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
        ledgered, "acc", PERIOD, outcome.authorized, ZERO, NOW, BUDGET_RULES
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
