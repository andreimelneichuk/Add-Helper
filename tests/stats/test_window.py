"""Период статистики: границы включены, сравнение с тем же числом дней до него."""

from datetime import date

import pytest

from adhelper.stats.application.overview import BadPeriod, Window, window

TODAY = date(2026, 11, 10)


def test_default_is_last_14_days() -> None:
    assert window(TODAY, None, None) == Window(date(2026, 10, 28), TODAY)


def test_previous_has_same_length_and_ends_the_day_before() -> None:
    period = Window(date(2026, 11, 1), date(2026, 11, 10))
    assert period.length == 10
    assert period.previous() == Window(date(2026, 10, 22), date(2026, 10, 31))
    assert Window(TODAY, TODAY).previous() == Window(date(2026, 11, 9), date(2026, 11, 9))


def test_since_alone_runs_to_today() -> None:
    assert window(TODAY, date(2026, 11, 3), None) == Window(date(2026, 11, 3), TODAY)


@pytest.mark.parametrize(
    ("since", "until"),
    [
        (date(2026, 11, 5), date(2026, 11, 4)),
        (None, date(2026, 11, 11)),
        (date(2025, 11, 9), TODAY),
    ],
)
def test_rejects_bad_periods(since: date | None, until: date | None) -> None:
    with pytest.raises(BadPeriod):
        window(TODAY, since, until)


def test_a_year_is_allowed() -> None:
    assert window(TODAY, date(2025, 11, 10), TODAY).length == 366
