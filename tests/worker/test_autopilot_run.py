"""Суточный цикл автопилота в воркере: анализ по витрине и предложение теста владельцу."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.settings import SqlAutopilotSettings
from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.autopilot.application.gate import PolicyGate
from adhelper.autopilot.domain.settings import DEFAULT_SETTINGS, AutopilotMode, Cadence
from adhelper.business.adapters.sql import SqlLeadGoal
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.domain.model import BusinessProfile, Service
from adhelper.worker.runtime import Runtime
from tests.autopilot.test_data import BUSINESS, OTHER
from tests.autopilot.test_data import _seed as _data  # noqa: F401  — витрина двух бизнесов
from tests.budget.fake_platform import FakePlatform

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
Sessions = async_sessionmaker[AsyncSession]


def runtime(sessions: Sessions, engine: AsyncEngine, connected: set[uuid.UUID]) -> Runtime:
    platform = FakePlatform([], NOW)
    return Runtime(
        sessions=sessions,
        engine=engine,
        platform_for=lambda business, _account: platform if business in connected else None,
        clock=lambda: NOW,
    )


async def test_borderline_ad_gets_test_offer_and_nothing_changes(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    # Объявление «1»: 28 дозревших дней по 100 ₽ и по обращению — цена 100 ₽ при цели 90 ₽.
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    profile = BusinessProfile("Лак", (Service("Маникюр", 1500),))
    await SqlProfiles(sessions, BUSINESS).save(profile, "")

    done = await runtime(sessions, engine, {BUSINESS}).autopilot_run()

    assert done == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None
    assert (decision.action, decision.status) == ("start_test", "awaiting_confirmation")
    assert decision.inputs["hypothesis"] == "price"
    assert decision.rules_version == "autopilot-5"
    assert await SqlDecisionLog(sessions, OTHER).latest("acc", "ad:1") is None


async def test_business_without_goal_or_platform_is_skipped(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await SqlLeadGoal(sessions, OTHER).set(90_000_000)
    assert await runtime(sessions, engine, {BUSINESS}).autopilot_run() == 0
    assert await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1") is None


async def _found_counter(sessions: Sessions, business: uuid.UUID, counter: int) -> None:
    from adhelper.business.adapters.rules_file import load_tag_rules
    from adhelper.business.adapters.tag_checks import SqlTagCheckQueue, SqlTagChecks
    from adhelper.business.application.tag import run_tag_checks
    from tests.business.test_tag import Pages

    rules = load_tag_rules()
    await SqlTagChecks(sessions, business).request("https://lak.example/", rules, NOW)
    await run_tag_checks(SqlTagCheckQueue(sessions), Pages([counter]), rules, lambda: NOW)


async def test_zero_visits_from_metrika_stop_conclusions_honestly(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    await _found_counter(sessions, BUSINESS, 98765432)
    metrika = FakePlatform([], NOW)
    metrika.visits = {NOW.date(): 0}
    worker = runtime(sessions, engine, {BUSINESS})
    worker.visits_for = lambda business, _account: metrika if business == BUSINESS else None

    assert await worker.autopilot_run() == 1

    assert metrika.visit_calls[0][0] == "98765432"
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "acc")
    assert decision is not None
    assert (decision.verdict, decision.status) == ("untrusted", "protect_only")
    assert decision.inputs["reasons"] == ["tracking_lost"]
    # По объявлению вывода нет: при сломанном счётчике тест не предлагается.
    assert await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1") is None


async def test_metrika_without_data_keeps_cycle_as_before(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    await SqlProfiles(sessions, BUSINESS).save(
        BusinessProfile("Лак", (Service("Маникюр", 1500),)), ""
    )
    await _found_counter(sessions, BUSINESS, 98765432)
    metrika = FakePlatform([], NOW)
    worker = runtime(sessions, engine, {BUSINESS})
    worker.visits_for = lambda _business, _account: metrika

    assert await worker.autopilot_run() == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None and decision.action == "start_test"


async def _borderline(sessions: Sessions) -> None:
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    await SqlProfiles(sessions, BUSINESS).save(
        BusinessProfile("Лак", (Service("Маникюр", 1500),)), ""
    )


async def _decision(sessions: Sessions) -> object:
    return await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")


async def test_a_second_tick_in_the_same_slot_does_nothing(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await _borderline(sessions)
    worker = runtime(sessions, engine, {BUSINESS})
    assert await worker.autopilot_run() == 1
    assert await worker.autopilot_run() == 0
    stored = await SqlAutopilotSettings(sessions, BUSINESS).get()
    assert stored is not None and stored.last_run_at == NOW
    assert stored.mode is AutopilotMode.CONFIRM


async def test_weekly_cadence_waits_for_the_week(sessions: Sessions, engine: AsyncEngine) -> None:
    await _borderline(sessions)
    store = SqlAutopilotSettings(sessions, BUSINESS)
    await store.save(replace(DEFAULT_SETTINGS, cadence=Cadence.WEEKLY), [])
    worker = runtime(sessions, engine, {BUSINESS})
    assert await worker.autopilot_run() == 1
    worker.clock = lambda: NOW + timedelta(days=6)
    assert await worker.autopilot_run() == 0
    worker.clock = lambda: NOW + timedelta(days=7)
    assert await worker.autopilot_run() == 1


async def test_stopped_autopilot_does_not_run(sessions: Sessions, engine: AsyncEngine) -> None:
    await _borderline(sessions)
    await SqlAutopilotSettings(sessions, BUSINESS).save(
        replace(DEFAULT_SETTINGS, stopped_at=NOW), []
    )
    assert await runtime(sessions, engine, {BUSINESS}).autopilot_run() == 0
    assert await _decision(sessions) is None


async def test_watch_mode_only_analyses_and_proposes_nothing(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await _borderline(sessions)
    await SqlAutopilotSettings(sessions, BUSINESS).save(
        replace(DEFAULT_SETTINGS, mode=AutopilotMode.WATCH), []
    )
    assert await runtime(sessions, engine, {BUSINESS}).autopilot_run() == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None and decision.action is None


async def test_auto_mode_in_the_database_still_only_proposes_while_not_allowed(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await _borderline(sessions)
    await SqlAutopilotSettings(sessions, BUSINESS).save(
        replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO), []
    )
    worker = runtime(sessions, engine, {BUSINESS})
    assert worker.auto_allowed is False
    await worker.autopilot_run()
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None and decision.status == "awaiting_confirmation"


async def test_shadow_rows_appear_beside_real_decisions_and_do_not_replace_them(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    profile = BusinessProfile("Лак", (Service("Маникюр", 1500),))
    await SqlProfiles(sessions, BUSINESS).save(profile, "")

    await runtime(sessions, engine, {BUSINESS}).autopilot_run()

    log = SqlDecisionLog(sessions, BUSINESS)
    real = await log.latest("acc", "ad:1")
    assert real is not None and real.status == "awaiting_confirmation"
    shadow = await log.shadow("acc")
    assert shadow, "autopilot-3 считает в тени на тех же данных"
    assert {row.status for row in shadow} == {"shadow"}
    assert {row.rules_version for row in shadow} == {"autopilot-3"}
    assert all(row.object_ref.startswith("shadow:") for row in shadow)
    assert not [row for row in await log.recent("acc") if row.status == "shadow"]
    assert await SqlDecisionLog(sessions, OTHER).shadow("acc") == []


async def test_confirmed_economics_sets_the_goal_of_the_cycle(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    from datetime import UTC, datetime

    from adhelper.business.adapters.economics import SqlEconomics
    from adhelper.business.adapters.rules_file import load_economics_rules
    from adhelper.business.domain.economics import goal_micros, reference

    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    economics = reference(load_economics_rules())
    check = 3000 * 1_000_000
    run_time = runtime(sessions, engine, {BUSINESS})
    expected = goal_micros(check, economics)
    assert expected is not None and expected != 90_000_000

    # Пока владелец не подтвердил цифры, цель прежняя.
    await SqlEconomics(sessions, BUSINESS).save(economics, check, None)
    assert await run_time.autopilot_goal(BUSINESS, 90_000_000) == 90_000_000
    # Средний чек из профиля, а не названный владельцем, цель не меняет.
    await SqlEconomics(sessions, BUSINESS).save(economics, None, datetime.now(UTC))
    assert await run_time.autopilot_goal(BUSINESS, 90_000_000) == 90_000_000
    await SqlEconomics(sessions, BUSINESS).save(economics, check, datetime.now(UTC))
    assert await run_time.autopilot_goal(BUSINESS, 90_000_000) == expected
    # Без записи экономики остаётся прежняя цель.
    assert await run_time.autopilot_goal(OTHER, 77_000_000) == 77_000_000


async def test_broken_shadow_rules_file_does_not_break_the_working_cycle(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adhelper.autopilot.adapters.rules_file import load_rules
    from adhelper.worker import runtime as runtime_module

    real = load_rules

    def load(version: str) -> Any:
        if version == runtime_module.SHADOW_AUTOPILOT_RULES:
            raise ValueError("файл правил испорчен")
        return real(version)

    monkeypatch.setattr(runtime_module, "load_autopilot_rules", load, raising=False)
    cycle = runtime(sessions, engine, {BUSINESS}).autopilot_cycle(BUSINESS, "acc")
    assert cycle is not None


async def test_working_cycle_has_the_policy_gate(sessions: Sessions, engine: AsyncEngine) -> None:
    """Ворота политики подключены в боевой цикл: без них действие шло бы мимо политики."""
    cycle = runtime(sessions, engine, {BUSINESS}).autopilot_cycle(BUSINESS, "acc")
    assert isinstance(cycle._gate, PolicyGate)
    assert cycle._gate._rules.version == "policy-1"


async def test_broken_policy_file_closes_the_gate_instead_of_opening_it(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adhelper.autopilot.application.gate import ClosedGate
    from adhelper.worker import runtime as runtime_module

    def broken(version: str) -> Any:
        raise ValueError("файл политики испорчен")

    monkeypatch.setattr(runtime_module, "load_policy_rules", broken)

    cycle = runtime(sessions, engine, {BUSINESS}).autopilot_cycle(BUSINESS, "acc")

    # Нет ворот — действие шло бы мимо политики; нет файла — самостоятельно не делаем ничего.
    assert isinstance(cycle._gate, ClosedGate)


async def _open_transfer(
    sessions: Sessions, platform: FakePlatform, rules_version: str = "policy-1"
) -> str:
    from adhelper.autopilot.adapters.transfers import SqlTransferStore
    from adhelper.autopilot.domain.transfers import TransferRecord
    from adhelper.budget.adapters.sql import SqlPeriodStore
    from adhelper.budget.domain.limits import BudgetPeriod

    period = BudgetPeriod(1_000_000, 1000, NOW - timedelta(days=1), NOW + timedelta(days=29))
    await SqlPeriodStore(sessions).add(BUSINESS, "acc", period, "budget-1", None, NOW)
    record = TransferRecord(
        str(uuid.uuid4()), "acc", "a", "b", 500_000, 490_000, 110_000, 10_000,
        {"reason": None, "rules_version": rules_version, "inputs": {}},
    )  # fmt: skip
    await SqlTransferStore(sessions, BUSINESS).add(record)
    return record.id


async def test_hourly_tick_finishes_an_interrupted_transfer(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    from adhelper.autopilot.adapters.transfers import SqlTransferStore
    from adhelper.autopilot.domain.transfers import TransferState
    from adhelper.budget.domain.limits import CampaignBudget
    from tests.budget.fake_platform import FakePlatform as Fake

    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    # Донор уже снижен, до получателя рост не дошёл: сервис остановился между шагами.
    platform = Fake(
        [CampaignBudget("a", 490_000, 0, True, None), CampaignBudget("b", 100_000, 0, True, None)],
        NOW,
    )
    transfer_id = await _open_transfer(sessions, platform)
    worker = Runtime(
        sessions=sessions,
        engine=engine,
        platform_for=lambda business, _account: platform if business == BUSINESS else None,
        clock=lambda: NOW,
    )

    await worker.autopilot_run()

    assert platform.campaigns["b"].limit == 110_000
    assert platform.campaigns["a"].limit == 490_000
    record = await SqlTransferStore(sessions, BUSINESS).get(transfer_id)
    assert record is not None and record.state is TransferState.DONE


async def test_failing_recovery_does_not_stop_the_cycle(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adhelper.worker import runtime as runtime_module

    await SqlLeadGoal(sessions, BUSINESS).set(90_000_000)
    await SqlProfiles(sessions, BUSINESS).save(
        BusinessProfile("Лак", (Service("Маникюр", 1500),)), ""
    )

    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("сбой доводчика")

    monkeypatch.setattr(runtime_module, "finish_open_transfers", boom)

    assert await runtime(sessions, engine, {BUSINESS}).autopilot_run() == 1


async def _auto_worker(sessions: Sessions, engine: AsyncEngine) -> Runtime:
    await _borderline(sessions)
    await SqlAutopilotSettings(sessions, BUSINESS).save(
        replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO), []
    )
    worker = runtime(sessions, engine, {BUSINESS})
    worker.auto_allowed = True
    return worker


async def test_auto_runs_as_auto_when_outcome_monitor_works(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    worker = await _auto_worker(sessions, engine)
    assert await worker.autopilot_run() == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None and decision.status != "awaiting_confirmation"


async def test_auto_falls_back_to_confirm_when_outcome_monitor_fails(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    worker = await _auto_worker(sessions, engine)

    def broken(business_id: uuid.UUID, account: str) -> Any:
        raise RuntimeError("monitor down")

    worker.autopilot_monitor = broken  # type: ignore[method-assign]
    assert await worker.autopilot_run() == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None and decision.status == "awaiting_confirmation"


async def test_auto_falls_back_to_confirm_while_data_is_untrusted(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    from adhelper.notifications.adapters.sql import SqlAlerts

    worker = await _auto_worker(sessions, engine)
    await SqlAlerts(sessions, BUSINESS).raise_alert("data_untrusted", {})
    assert await worker.autopilot_run() == 1
    decision = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:1")
    assert decision is not None and decision.status in {"awaiting_confirmation", "protect_only"}


async def test_hourly_tick_is_journaled_once_per_hour(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    from sqlalchemy import text

    await _borderline(sessions)
    worker = runtime(sessions, engine, {BUSINESS})
    await worker.autopilot_run()
    await worker.autopilot_run()
    async with engine.connect() as connection:
        rows = (await connection.execute(text("SELECT slot, accounts FROM autopilot_ticks"))).all()
    assert [r.slot for r in rows] == [NOW.replace(minute=0)]
    # Ручной разбор одного кабинета в журнал часовых тиков не пишет.
    await worker.autopilot_run(only=BUSINESS)
    async with engine.connect() as connection:
        count = await connection.scalar(text("SELECT count(*) FROM autopilot_ticks"))
    assert count == 1


async def test_auto_cabinet_acts_only_inside_granted_classes_and_envelope(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Владелец кабинета отметил рост лимита, но владелец проекта его не открыл: нельзя."""
    from adhelper.autopilot.application.cycle import AutopilotCycle
    from adhelper.autopilot.domain.decide import ActionKind

    await _borderline(sessions)
    ticked = frozenset(ActionKind)
    envelope = {"max_changes_per_day": 1}
    await SqlAutopilotSettings(sessions, BUSINESS).save(
        replace(
            DEFAULT_SETTINGS,
            mode=AutopilotMode.AUTO,
            allowed_actions=ticked,
            envelope=envelope,
            auto_since=NOW,
        ),
        [],
    )
    seen: dict[str, Any] = {}
    real_run = AutopilotCycle.run

    async def spy(self: AutopilotCycle, account: str, target: int, mode: Any, *rest: Any) -> None:
        seen["rest"] = rest
        await real_run(self, account, target, mode, *rest)

    monkeypatch.setattr(AutopilotCycle, "run", spy)
    worker = runtime(sessions, engine, {BUSINESS})
    worker.auto_allowed = True

    await worker.autopilot_run()

    allowed, passed_envelope = seen["rest"]
    assert allowed == {ActionKind.PAUSE_AD, ActionKind.START_TEST, ActionKind.INCREASE_LIMIT}
    assert passed_envelope == envelope


async def _digest_calls(
    sessions: Sessions,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: Any,
    auto_allowed: bool = True,
    later: timedelta = timedelta(0),
) -> list[bool]:
    from adhelper.autopilot.application.outcomes import OutcomeMonitor

    await _borderline(sessions)
    calls: list[bool] = []

    async def digest(self: OutcomeMonitor, account: str, *, always: bool) -> bool:
        calls.append(always)
        return False

    monkeypatch.setattr(OutcomeMonitor, "digest", digest)
    await SqlAutopilotSettings(sessions, BUSINESS).save(settings, [])
    worker = runtime(sessions, engine, {BUSINESS})
    worker.auto_allowed = auto_allowed
    worker.clock = lambda: NOW + later
    await worker.autopilot_run()
    return calls


async def test_daily_report_is_sent_in_the_first_14_days_of_auto(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    auto = replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, auto_since=NOW - timedelta(days=3))

    assert True in await _digest_calls(sessions, engine, monkeypatch, settings=auto)


async def test_daily_report_stops_after_14_days(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    auto = replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, auto_since=NOW - timedelta(days=3))

    calls = await _digest_calls(
        sessions, engine, monkeypatch, settings=auto, later=timedelta(days=12)
    )

    assert True not in calls  # 15 суток от включения


async def test_daily_report_does_not_wait_for_a_weekly_cadence(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adhelper.autopilot.application.outcomes import OutcomeMonitor

    # Разбор раз в неделю: сегодняшний слот уже отработан, а письмо за сутки всё равно уходит.
    weekly = replace(
        DEFAULT_SETTINGS,
        mode=AutopilotMode.AUTO,
        cadence=Cadence.WEEKLY,
        auto_since=NOW - timedelta(days=3),
        last_run_at=NOW - timedelta(hours=2),
    )
    calls: list[bool] = []

    async def digest(self: OutcomeMonitor, account: str, *, always: bool) -> bool:
        calls.append(always)
        return False

    monkeypatch.setattr(OutcomeMonitor, "digest", digest)
    await _borderline(sessions)
    await SqlAutopilotSettings(sessions, BUSINESS).save(weekly, [])
    worker = runtime(sessions, engine, {BUSINESS})
    worker.auto_allowed = True
    assert await worker.autopilot_run() == 0  # цикл по расписанию не идёт

    assert calls == [True]


async def test_daily_report_is_not_sent_while_auto_is_not_allowed(
    sessions: Sessions, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    auto = replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, auto_since=NOW - timedelta(days=3))

    calls = await _digest_calls(sessions, engine, monkeypatch, settings=auto, auto_allowed=False)

    assert True not in calls


async def test_header_stop_keeps_an_auto_cabinet_quiet(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Выключатель в шапке работает и в «Сам»: остановленный кабинет не разбирается."""
    from adhelper.autopilot.application.settings import stop

    worker = await _auto_worker(sessions, engine)
    await stop(SqlAutopilotSettings(sessions, BUSINESS), NOW)
    assert await worker.autopilot_run() == 0
    assert await _decision(sessions) is None
