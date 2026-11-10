"""Сценарии 4–7 """

from datetime import UTC, datetime, timedelta

from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.guard import TransferStatus, WatchState, drive_transfer, watch_once
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, LimitChange
from adhelper.budget.domain.transfer import plan_transfer
from adhelper.budget.domain.watch import BurnRate, WatchAction
from tests.budget.fake_platform import FakePlatform, Fault

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RULES = load_rules("budget-1")
HARD = 1_000_000_000
PERIOD = BudgetPeriod(hard_limit=HARD, reserve_bp=1000)
ZERO = BurnRate.zero()


def campaign(campaign_id: str, limit: int, spent: int = 0) -> CampaignBudget:
    return CampaignBudget(campaign_id, limit, spent, True, None)


async def test_scenario_4_spike_with_stale_stats_stops_before_threshold() -> None:
    """Слой 2 сломан, статистика отстаёт на час: остановка срабатывает по прогнозу."""
    platform = FakePlatform(
        [campaign("a", 450_000_000), campaign("b", 450_000_000)],
        NOW,
        stats_lag=timedelta(hours=1),
        enforces_limits=False,
    )
    state = WatchState.initial()
    stopped = False
    for _ in range(80):
        platform.run(minutes=15, per_minute=500_000)
        result = await watch_once(platform, "acc", PERIOD, state, platform.now, RULES)
        state = result.state
        if result.stopped:
            stopped = True
            break

    assert stopped
    assert result.verdict is not None
    assert result.verdict.action is WatchAction.STOP
    assert not any(c.active for c in platform.campaigns.values())
    # По отчёту ещё далеко от порога, а реальный расход уже у него, но не выше лимита.
    assert state.snapshot is not None
    assert state.snapshot.reported < 0.97 * HARD
    assert platform.total_spent <= HARD

    spent_at_stop = platform.total_spent
    platform.run(minutes=60, per_minute=500_000)
    assert platform.total_spent == spent_at_stop


async def test_scenario_5_api_down_platform_limit_protects_budget() -> None:
    """Наш сервис ничего не может сделать, расход упирается в лимит площадки."""
    platform = FakePlatform([campaign("a", 450_000_000), campaign("b", 450_000_000)], NOW)
    platform.unavailable = True
    state = WatchState.initial()

    for _ in range(100):
        platform.run(minutes=15, per_minute=2_000_000)
        result = await watch_once(platform, "acc", PERIOD, state, platform.now, RULES)
        state = result.state
        assert result.platform_unavailable
        assert not result.stopped

    assert platform.total_spent == PERIOD.operating_budget
    assert platform.total_spent <= HARD
    assert all(c.active for c in platform.campaigns.values())


async def test_scenario_5_stop_is_retried_when_api_returns() -> None:
    platform = FakePlatform([campaign("a", 2_000_000_000)], NOW, enforces_limits=False)
    platform.run(minutes=1, per_minute=980_000_000)
    platform.faults.append(Fault("pause_campaigns"))

    first = await watch_once(platform, "acc", PERIOD, WatchState.initial(), platform.now, RULES)
    assert first.stop_pending
    assert not first.stopped
    assert platform.campaigns["a"].active

    second = await watch_once(platform, "acc", PERIOD, first.state, platform.now, RULES)
    assert second.stopped
    assert not platform.campaigns["a"].active


async def test_scenario_6_crash_between_limit_change_and_record() -> None:
    """Лимит изменён, ответ потерян: согласование не применяет его повторно."""
    start = [campaign("a", 500_000_000), campaign("b", 100_000_000)]
    platform = FakePlatform(start, NOW)
    platform.faults.append(Fault("set_limit", after_apply=True))
    plan = plan_transfer(start[0], start[1], 20_000_000)

    crashed = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, RULES)
    assert crashed is TransferStatus.INTERRUPTED
    assert platform.set_limit_calls == [LimitChange("a", 480_000_000)]

    resumed = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, RULES)

    assert resumed is TransferStatus.DONE
    assert platform.set_limit_calls == [
        LimitChange("a", 480_000_000),
        LimitChange("b", 120_000_000),
    ]


async def test_scenario_7_failure_on_second_step_leaves_sum_lower() -> None:
    start = [campaign("a", 500_000_000), campaign("b", 100_000_000)]
    platform = FakePlatform(start, NOW)
    plan = plan_transfer(start[0], start[1], 20_000_000)
    # Первый вызов set_limit проходит, второй падает до применения.
    original = platform.set_limit
    calls = 0

    async def fail_second(account: str, change: LimitChange) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            platform.faults.append(Fault("set_limit"))
        await original(account, change)

    platform.set_limit = fail_second  # type: ignore[method-assign]

    status = await drive_transfer(platform, "acc", PERIOD, plan, ZERO, NOW, RULES)

    assert status is TransferStatus.INTERRUPTED
    assert platform.limit_sum == 580_000_000
    assert max(platform.limit_sums) == 600_000_000
    assert platform.campaigns["b"].limit == 100_000_000
