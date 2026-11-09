"""Симулятор на истории: две версии правил на одних днях. docs/04-autopilot.md §4."""

from datetime import date, timedelta
from itertools import pairwise

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.application.simulator import AdDay, Day, compare, simulate
from adhelper.autopilot.domain.decide import ActionKind
from adhelper.autopilot.domain.health import HealthReason

OLD = load_rules("autopilot-1")
NEW = load_rules("autopilot-2")
TARGET = 300_000_000
START = date(2026, 9, 1)


def borderline_days(count: int, visits: int | None = 50) -> list[Day]:
    # Тратит больше цели, но обращения изредка есть: вывода нет, нужен тест.
    return [
        Day(
            START + timedelta(days=i),
            [AdDay("ad:m", "c1", "ad:m", 150_000_000, 1 if i % 3 == 0 else 0, 40, visits)],
        )
        for i in range(count)
    ]


def started(log: list) -> list[date]:  # type: ignore[type-arg]
    return [
        entry.at.date() for entry in log for a in entry.actions if a.kind is ActionKind.START_TEST
    ]


def test_same_rules_give_no_differences() -> None:
    assert compare(borderline_days(40), TARGET, NEW, NEW, {"c1": 600_000_000}) == []


def test_small_limit_stops_test_only_under_new_rules() -> None:
    # 7 дней по 100 ₽ не покрывают 4 цены по 300 ₽: autopilot-2 тест не начинает.
    differences = compare(borderline_days(40), TARGET, OLD, NEW, {"c1": 100_000_000})
    assert differences
    assert {(d.ref, d.before, d.after) for d in differences} == {("ad:m", "start_test", None)}


def test_running_test_is_not_started_again_until_it_ends() -> None:
    log = simulate(borderline_days(40), TARGET, NEW, {"c1": 600_000_000})
    days = started(log)
    assert days, "пограничное объявление должно получить тест"
    assert all((b - a).days >= NEW.test_days for a, b in pairwise(days))


def test_unknown_visits_in_history_are_not_an_alarm() -> None:
    # Ноль визитов при кликах — тревога; неизвестные визиты — нет.
    visit_alarms = {HealthReason.TRACKING_LOST, HealthReason.VISITS_MISMATCH}
    zero = simulate(borderline_days(40, visits=0), TARGET, NEW, {"c1": 600_000_000})
    assert any(visit_alarms & set(entry.health.reasons) for entry in zero)
    unknown = simulate(borderline_days(40, visits=None), TARGET, NEW, {"c1": 600_000_000})
    assert not any(visit_alarms & set(entry.health.reasons) for entry in unknown)


def test_ad_without_rows_that_day_is_not_judged() -> None:
    # Объявление выключили вне симулятора: строк больше нет, решений по нему тоже.
    days = borderline_days(40)
    stopped = [Day(day.day, []) if i >= 10 else day for i, day in enumerate(days)]
    log = simulate(stopped, TARGET, OLD, {"c1": 600_000_000})
    assert all(not entry.actions for entry in log[10:])


def test_autopilot_3_is_compared_with_autopilot_2_on_the_same_history() -> None:
    """Движки разные, симулятор один: расхождение объяснимо цифрами клика и обращений."""
    before, after = load_rules("autopilot-2"), load_rules("autopilot-3")
    # Один пограничный и один явно слабый вариант в направлении, 20 дней.
    days = [
        Day(
            START + timedelta(days=i),
            [
                AdDay("ad:a", "c1", "g1", 70_000_000, 3 if i % 2 == 0 else 2, 60, None),
                AdDay("ad:b", "c1", "g1", 70_000_000, 1 if i % 4 == 0 else 0, 60, None),
            ],
        )
        for i in range(20)
    ]
    first = compare(days, TARGET, before, after, {"c1": 600_000_000})
    again = compare(days, TARGET, before, after, {"c1": 600_000_000})
    assert first == again, "воспроизводимо"
    log = simulate(days, TARGET, after, {"c1": 600_000_000})
    paused = [a for entry in log for a in entry.actions if a.kind is ActionKind.PAUSE_AD]
    assert paused and all(a.rules_version == "autopilot-3" for a in paused)
    assert all(
        a.ref == "ad:b" and a.facts["p_worse_bp"] >= after.min_worse_probability_bp for a in paused
    )
