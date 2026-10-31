"""Сценарии 1–3 из docs/07-testing-acceptance.md §2 на симуляторе."""

from datetime import UTC, date, datetime, timedelta

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.application.simulator import AdDay, Day, simulate
from adhelper.autopilot.domain.decide import ActionKind
from adhelper.autopilot.domain.health import HealthReason

RULES = load_rules("autopilot-1")
TARGET = 400_000_000
START = date(2026, 10, 1)
DAY_SPEND = 200_000_000


def steady(ref: str, leads_every: int, day: int, spend: int = DAY_SPEND) -> AdDay:
    return AdDay(ref, "c1", "g1", spend, 1 if day % leads_every == 0 else 0, 60, 55)


def day_at(index: int, ads: list[AdDay], incident: bool = False) -> Day:
    return Day(START + timedelta(days=index), ads, incident)


def calendar(days: int, build: "object") -> list[Day]:
    return [build(i) for i in range(days)]  # type: ignore[operator]


def paused(log: list) -> list[str]:  # type: ignore[type-arg]
    return [a.ref for entry in log for a in entry.actions if a.kind is ActionKind.PAUSE_AD]


def test_control_truly_bad_ad_is_paused() -> None:
    """Без этой проверки сценарии ниже прошли бы у автопилота, который ничего не делает."""

    def build(i: int) -> Day:
        return day_at(i, [steady("ad:good", 2, i), steady("ad:bad", 1000, i + 1)])

    assert "ad:bad" in paused(simulate(calendar(40, build), TARGET, RULES))


def test_scenario_1_bad_market_week_does_not_stop_good_ad() -> None:
    def build(i: int) -> Day:
        leads_every = 2 if i < 33 else 1000  # Последняя неделя слабая у всех сразу.
        return day_at(
            i,
            [steady("ad:a", leads_every, i + 1), steady("ad:b", leads_every, i + 1)],
        )

    log = simulate(calendar(40, build), TARGET, RULES)

    assert paused(log) == []


def test_scenario_1_single_weak_week_of_good_ad_does_not_stop_it() -> None:
    def build(i: int) -> Day:
        leads_every = 2 if i < 33 else 1000
        return day_at(i, [steady("ad:a", leads_every, i + 1), steady("ad:b", 2, i + 1)])

    assert paused(simulate(calendar(40, build), TARGET, RULES)) == []


def test_scenario_2_removed_counter_raises_alert_not_pause() -> None:
    def build(i: int) -> Day:
        ad_a, ad_b = steady("ad:a", 2, i + 1), steady("ad:b", 2, i + 1)
        if i >= 30:  # Код статистики удалён: клики идут, визитов и обращений нет.
            ad_a = AdDay("ad:a", "c1", "g1", DAY_SPEND, 0, 60, 0)
            ad_b = AdDay("ad:b", "c1", "g1", DAY_SPEND, 0, 60, 0)
        return day_at(i, [ad_a, ad_b])

    log = simulate(calendar(40, build), TARGET, RULES)

    assert paused(log) == []
    assert HealthReason.TRACKING_LOST in log[-1].health.reasons
    assert log[-1].protect_only


def test_scenario_3_outage_switches_to_protection_and_later_recovers() -> None:
    def build(i: int) -> Day:
        outage = 30 <= i < 37
        leads = 0 if outage else None
        ads = [steady("ad:a", 2, i + 1), steady("ad:b", 2, i + 1)]
        if leads == 0:
            ads = [AdDay(a.ref, "c1", "g1", a.spend, 0, a.clicks, a.visits) for a in ads]
        return day_at(i, ads, incident=outage)

    log = simulate(calendar(50, build), TARGET, RULES)

    assert paused(log) == []
    assert all(entry.protect_only for entry in log[30:37])
    assert not log[-1].protect_only


def test_scenario_3_gap_without_incident_flag_is_still_caught() -> None:
    """Флаг службы могут включить поздно: разрыв конверсий ловим сами."""

    def build(i: int) -> Day:
        ads = [steady("ad:a", 2, i + 1), steady("ad:b", 2, i + 1)]
        if i >= 30:
            ads = [AdDay(a.ref, "c1", "g1", a.spend, 0, a.clicks, a.visits) for a in ads]
        return day_at(i, ads)

    log = simulate(calendar(40, build), TARGET, RULES)

    assert paused(log) == []
    assert any(HealthReason.CONVERSION_GAP in entry.health.reasons for entry in log)


def test_simulator_uses_day_end_clock() -> None:
    log = simulate(calendar(1, lambda i: day_at(i, [steady("ad:a", 2, i)])), TARGET, RULES)
    assert log[0].at == datetime(2026, 10, 1, 20, 59, tzinfo=UTC)


def test_scenario_3_long_outage_is_not_counted_after_recovery() -> None:
    """Дни сбоя исключены из оценки: иначе после восстановления отключили бы хорошую рекламу."""

    def build(i: int) -> Day:
        ads = [steady("ad:a", 2, i + 1), steady("ad:b", 2, i + 1)]
        outage = 30 <= i < 70
        if outage:
            ads = [AdDay(a.ref, "c1", "g1", a.spend, 0, a.clicks, a.visits) for a in ads]
        return day_at(i, ads, incident=outage)

    assert paused(simulate(calendar(75, build), TARGET, RULES)) == []
