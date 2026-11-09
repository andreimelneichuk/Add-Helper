import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.guard import (
    ChangeStatus,
    TransferStatus,
    apply_limit_change,
    drive_transfer,
)
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, LimitChange
from adhelper.budget.domain.transfer import TransferRejected, next_step, plan_transfer
from adhelper.budget.domain.watch import BurnRate
from tests.budget.fake_platform import FakePlatform, Fault

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
# Шаг и частоту снимаем, чтобы последовательности доходили до проверки суммы лимитов.
RULES = replace(load_rules("budget-1"), max_increase_bp=1_000_000, min_increase_interval_days=0)
PERIOD = BudgetPeriod(hard_limit=1_000_000, reserve_bp=1000)
ZERO = BurnRate.zero()


def campaign(campaign_id: str, limit: int, spent: int = 0) -> CampaignBudget:
    return CampaignBudget(campaign_id, limit, spent, True, None)


def test_plan_moves_amount_between_campaigns() -> None:
    plan = plan_transfer(campaign("a", 500), campaign("b", 100), 200)

    assert (plan.donor_target, plan.recipient_target) == (300, 300)


@pytest.mark.parametrize(
    ("amount", "spent"),
    [(0, 0), (-1, 0), (501, 0), (200, 301)],
)
def test_plan_rejects_invalid_amounts(amount: int, spent: int) -> None:
    with pytest.raises(TransferRejected):
        plan_transfer(campaign("a", 500, spent), campaign("b", 100), amount)


def test_plan_rejects_transfer_to_self() -> None:
    with pytest.raises(TransferRejected):
        plan_transfer(campaign("a", 500), campaign("a", 500), 1)


def test_recipient_waits_until_donor_decrease_is_confirmed() -> None:
    plan = plan_transfer(campaign("a", 500), campaign("b", 100), 200)

    assert next_step(plan, {"a": 500, "b": 100}) == LimitChange("a", 300)
    assert next_step(plan, {"a": 300, "b": 100}) == LimitChange("b", 300)
    assert next_step(plan, {"a": 300, "b": 300}) is None


async def test_transfer_decreases_before_increasing() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    plan = plan_transfer(campaign("a", 500_000), campaign("b", 100_000), 50_000)

    status = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, RULES)

    assert status is TransferStatus.DONE
    assert platform.set_limit_calls == [LimitChange("a", 450_000), LimitChange("b", 150_000)]


async def test_recipient_increase_is_rechecked_and_can_be_rejected() -> None:
    """Пока шёл перенос, в кабинете подняли лимит: рост получателя уже небезопасен."""
    platform = FakePlatform(
        [campaign("a", 500_000), campaign("b", 100_000), campaign("c", 500_000)], NOW
    )
    plan = plan_transfer(campaign("a", 500_000), campaign("b", 100_000), 50_000)

    status = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, RULES)

    assert status is TransferStatus.REJECTED
    # Рост получателя не пройдёт, поэтому донора не трогаем вовсе: деньги не остаются выведенными.
    assert platform.set_limit_calls == []
    assert platform.campaigns["a"].limit == 500_000


async def test_recipient_rejected_after_donor_cut_gives_donor_back() -> None:
    """Состояние изменилось между шагами: донор уже снижен, рост получателя отказан."""
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    plan = plan_transfer(campaign("a", 500_000), campaign("b", 100_000), 50_000)
    await platform.set_limit("acc", LimitChange("a", 450_000))
    platform.campaigns["c"] = campaign("c", 500_000)
    platform.set_limit_calls.clear()

    status = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, RULES)

    assert status is TransferStatus.STUCK
    assert platform.campaigns["a"].limit == 450_000


async def test_recipient_rejected_after_donor_cut_restores_donor_when_budget_allows() -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 100_000)], NOW)
    rules = replace(RULES, max_increase_bp=0)
    plan = plan_transfer(campaign("a", 500_000), campaign("b", 100_000), 50_000)
    await platform.set_limit("acc", LimitChange("a", 450_000))
    platform.set_limit_calls.clear()

    status = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, rules)

    assert status is TransferStatus.ROLLED_BACK
    assert platform.campaigns["a"].limit == 500_000
    assert platform.campaigns["b"].limit == 100_000
    assert platform.limit_sum <= PERIOD.operating_budget


async def test_repeat_of_applied_change_does_not_call_platform() -> None:
    platform = FakePlatform([campaign("a", 500_000)], NOW)
    change = LimitChange("a", 400_000)

    first = await apply_limit_change(platform, "acc", PERIOD, change, ZERO, NOW, RULES)
    second = await apply_limit_change(platform, "acc", PERIOD, change, ZERO, NOW, RULES)

    assert first.status is ChangeStatus.APPLIED
    assert second.status is ChangeStatus.ALREADY_APPLIED
    assert len(platform.set_limit_calls) == 1


async def test_lost_response_is_unconfirmed_and_retry_reads_first() -> None:
    platform = FakePlatform([campaign("a", 500_000)], NOW)
    platform.faults.append(Fault("set_limit", after_apply=True))
    change = LimitChange("a", 400_000)

    first = await apply_limit_change(platform, "acc", PERIOD, change, ZERO, NOW, RULES)
    second = await apply_limit_change(platform, "acc", PERIOD, change, ZERO, NOW, RULES)

    assert first.status is ChangeStatus.UNCONFIRMED
    assert second.status is ChangeStatus.ALREADY_APPLIED
    assert len(platform.set_limit_calls) == 1


async def test_rejected_change_does_not_reach_platform() -> None:
    platform = FakePlatform([campaign("a", 500_000)], NOW)

    outcome = await apply_limit_change(
        platform, "acc", PERIOD, LimitChange("a", 900_001), ZERO, NOW, RULES
    )

    assert outcome.status is ChangeStatus.REJECTED
    assert outcome.decision is not None
    assert platform.set_limit_calls == []


operations = st.lists(
    st.tuples(
        st.sampled_from(["change", "transfer"]),
        st.integers(0, 3),
        st.integers(0, 3),
        st.integers(0, 1_200_000),
        st.sampled_from(
            [
                None,
                Fault("set_limit"),
                Fault("set_limit", after_apply=True),
                Fault("read_campaigns"),
                Fault("fetch_spend"),
            ]
        ),
    ),
    max_size=12,
)


async def _run_sequence(
    limits: list[int], ops: list[tuple[str, int, int, int, Fault | None]]
) -> FakePlatform:
    ids = [str(index) for index in range(len(limits))]
    platform = FakePlatform([campaign(i, limit) for i, limit in zip(ids, limits, strict=True)], NOW)
    for kind, first, second, value, fault in ops:
        if fault:
            platform.faults.append(replace(fault))
        donor = platform.campaigns[ids[first % len(ids)]]
        recipient = platform.campaigns[ids[second % len(ids)]]
        if kind == "change":
            change = LimitChange(donor.campaign_id, value)
            await apply_limit_change(platform, "acc", PERIOD, change, ZERO, NOW, RULES)
        else:
            try:
                plan = plan_transfer(donor, recipient, value)
            except TransferRejected:
                continue
            await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, RULES)
        platform.run(minutes=1, per_minute=100)
    return platform


@settings(max_examples=200, deadline=None)
@given(
    st.lists(st.integers(0, 225_000), min_size=1, max_size=4),
    operations,
)
def test_limit_sum_stays_within_operating_budget_after_every_call(
    limits: list[int], ops: list[tuple[str, int, int, int, Fault | None]]
) -> None:
    """Главное свойство: ни одна последовательность со сбоями не поднимает сумму выше O."""
    platform = asyncio.run(_run_sequence(limits, ops))

    assert max(platform.limit_sums) <= PERIOD.operating_budget
    assert platform.total_spent <= PERIOD.hard_limit


@settings(max_examples=200, deadline=None)
@given(
    st.integers(1, 400_000),
    st.integers(1, 400_000),
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
def test_interrupted_transfer_finishes_without_extra_calls(
    donor: int, recipient: int, amount: int, faults: list[Fault]
) -> None:
    async def scenario() -> None:
        first, second = campaign("a", donor), campaign("b", recipient)
        try:
            plan = plan_transfer(first, second, amount)
        except TransferRejected:
            return
        platform = FakePlatform([first, second], NOW)
        platform.faults = [replace(fault) for fault in faults]
        start_sum = platform.limit_sum

        # Без оценки всплеска: здесь проверяем возобновление, а не отказ по худшему случаю.
        rules = replace(RULES, change_spike_old_bp=0, change_spike_new_bp=0, max_increase_bp=10**12)
        status = TransferStatus.INTERRUPTED
        for _ in range(len(faults) + 1):
            status = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, rules)
            if status is not TransferStatus.INTERRUPTED:
                break

        assert status is TransferStatus.DONE
        assert platform.campaigns["a"].limit == plan.donor_target
        assert platform.campaigns["b"].limit == plan.recipient_target
        assert platform.limit_sum == start_sum
        # Сбой «до применения» не даёт вызова, поэтому записан каждый изменивший вызов один раз.
        assert len(platform.set_limit_calls) == 2
        assert max(platform.limit_sums) <= start_sum

    asyncio.run(scenario())
