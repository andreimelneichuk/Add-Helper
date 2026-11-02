"""Где и когда показывать: проверки до журнала и формат записи."""

from hypothesis import given
from hypothesis import strategies as st

from adhelper.campaigns.adapters.rules_file import load_targeting_rules
from adhelper.campaigns.domain.targeting import (
    Schedule,
    TargetingProblem,
    check_regions,
    check_schedule,
    describe_schedule,
)

RULES = load_targeting_rules("targeting-1")


def test_rules_file_has_threshold_and_regions() -> None:
    assert RULES.version == "targeting-1"
    assert RULES.min_weekly_hours == 8
    assert RULES.regions[213] == "Москва"


def test_every_day_working_hours_is_allowed() -> None:
    assert check_schedule(Schedule((0, 1, 2, 3, 4, 5, 6), 10, 21), RULES) is None


def test_schedule_without_days_is_refused() -> None:
    assert check_schedule(Schedule((), 10, 21), RULES) is TargetingProblem.NO_DAYS


def test_schedule_with_bad_hours_is_refused() -> None:
    assert check_schedule(Schedule((0,), 21, 10), RULES) is TargetingProblem.BAD_HOURS
    assert check_schedule(Schedule((0,), 0, 25), RULES) is TargetingProblem.BAD_HOURS
    assert check_schedule(Schedule((7,), 0, 24), RULES) is TargetingProblem.BAD_DAYS


def test_too_few_hours_a_week_is_refused() -> None:
    # Одно воскресенье на 8 часов — ровно порог Яндекса, на 7 — уже нет.
    assert check_schedule(Schedule((6,), 10, 18), RULES) is None
    assert check_schedule(Schedule((6,), 10, 17), RULES) is TargetingProblem.TOO_FEW_HOURS
    # Только выходные допустимы, если часов хватает.
    assert check_schedule(Schedule((5, 6), 0, 24), RULES) is None


def test_planned_form_round_trips_and_is_canonical() -> None:
    schedule = Schedule.of([6, 0, 0, 3], 9, 21)
    assert schedule.days == (0, 3, 6)
    assert schedule.as_planned() == {"days": [0, 3, 6], "hours": [9, 21]}
    assert Schedule.from_planned(schedule.as_planned()) == schedule


def test_regions_must_be_known_and_not_empty() -> None:
    assert check_regions((213,), RULES) is None
    assert check_regions((), RULES) is TargetingProblem.NO_REGIONS
    assert check_regions((213, 999_999), RULES) is TargetingProblem.UNKNOWN_REGION


def test_schedule_is_described_in_owner_words() -> None:
    assert describe_schedule(Schedule((0, 1, 2, 3, 4, 5, 6), 0, 24)) == "круглосуточно"
    assert describe_schedule(Schedule((0, 1, 2, 3, 4, 5, 6), 9, 21)) == ("каждый день, 9:00–21:00")
    assert describe_schedule(Schedule((0, 1, 2, 3, 4, 5), 10, 21)) == "пн–сб, 10:00–21:00"
    assert describe_schedule(Schedule((0, 2, 4), 10, 24)) == "пн, ср, пт, 10:00–24:00"


@given(
    st.lists(st.integers(0, 6), max_size=10),
    st.integers(-2, 26),
    st.integers(-2, 26),
)
def test_accepted_schedule_is_what_yandex_accepts(days: list[int], start: int, end: int) -> None:
    schedule = Schedule.of(days, start, end)
    if check_schedule(schedule, RULES) is not None:
        return
    assert schedule.days and 0 <= start < end <= 24
    assert len(schedule.days) * (end - start) >= RULES.min_weekly_hours


def test_vitrine_patch_and_summary() -> None:
    from adhelper.campaigns.application.targeting import summarize, vitrine_patch

    work = Schedule((0, 1, 2, 3, 4, 5), 10, 21)
    patch = vitrine_patch({"schedule": work.as_planned(), "regions": [213, 2]}, RULES)
    assert patch == {
        "schedule": "пн–сб, 10:00–21:00",
        "schedule_plan": {"days": [0, 1, 2, 3, 4, 5], "hours": [10, 21]},
        "regions": ["Санкт-Петербург", "Москва"],
        "region_ids": [2, 213],
    }
    same = summarize([patch, dict(patch, placement="search")])
    assert (same.schedule, same.regions, same.campaigns) == (work, (2, 213), 2)
    # Разные часы у кампаний или неизвестные: общего значения нет, экран так и скажет.
    differ = summarize([patch, {"schedule": "каждый день"}])
    assert (differ.schedule, differ.regions) == (None, None)
    assert summarize([]).schedule is None
