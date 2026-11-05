"""Где и когда показывать рекламу: нашими понятиями, без форматов площадки."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

DAYS = 7
DAY_NAMES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


@dataclass(frozen=True)
class TargetingRules:
    version: str
    # Меньше этого числа часов показа за неделю площадка расписание не принимает.
    min_weekly_hours: int
    # Справочник регионов площадки: номер → название для владельца.
    regions: Mapping[int, str]


class TargetingProblem(StrEnum):
    NO_DAYS = "no_days"
    BAD_DAYS = "bad_days"
    BAD_HOURS = "bad_hours"
    TOO_FEW_HOURS = "too_few_hours"
    NO_REGIONS = "no_regions"
    UNKNOWN_REGION = "unknown_region"


@dataclass(frozen=True)
class Schedule:
    """Дни недели (0 — понедельник) и часы показа [start, end) по часовому поясу кампании."""

    days: tuple[int, ...]
    start: int
    end: int

    @classmethod
    def of(cls, days: Iterable[int], start: int, end: int) -> "Schedule":
        return cls(tuple(sorted(set(days))), start, end)

    def as_planned(self) -> dict[str, list[int]]:
        return {"days": list(self.days), "hours": [self.start, self.end]}

    @classmethod
    def from_planned(cls, raw: Mapping[str, Any]) -> "Schedule":
        start, end = raw["hours"]
        return cls.of(raw["days"], int(start), int(end))


def check_schedule(schedule: Schedule, rules: TargetingRules) -> TargetingProblem | None:
    if not schedule.days:
        return TargetingProblem.NO_DAYS
    if any(day < 0 or day >= DAYS for day in schedule.days):
        return TargetingProblem.BAD_DAYS
    if not 0 <= schedule.start < schedule.end <= 24:
        return TargetingProblem.BAD_HOURS
    if len(schedule.days) * (schedule.end - schedule.start) < rules.min_weekly_hours:
        return TargetingProblem.TOO_FEW_HOURS
    return None


def canonical_regions(regions: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted(set(regions)))


def check_regions(regions: tuple[int, ...], rules: TargetingRules) -> TargetingProblem | None:
    if not regions:
        return TargetingProblem.NO_REGIONS
    if any(region not in rules.regions for region in regions):
        return TargetingProblem.UNKNOWN_REGION
    return None


def _days(days: tuple[int, ...]) -> str:
    if len(days) == DAYS:
        return "каждый день"
    first, last = days[0], days[-1]
    if len(days) > 2 and days == tuple(range(first, last + 1)):
        return f"{DAY_NAMES[first]}–{DAY_NAMES[last]}"
    return ", ".join(DAY_NAMES[day] for day in days)


def describe_schedule(schedule: Schedule) -> str:
    if len(schedule.days) == DAYS and (schedule.start, schedule.end) == (0, 24):
        return "круглосуточно"
    return f"{_days(schedule.days)}, {schedule.start}:00–{schedule.end}:00"
