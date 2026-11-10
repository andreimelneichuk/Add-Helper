"""Лестница доверия: что кабинету разрешено делать самому и когда открывается следующая ступень."""

from datetime import UTC, datetime, timedelta

from adhelper.autopilot.domain.decide import ActionKind
from adhelper.autopilot.domain.trust import (
    START_CLASSES,
    GrantRefusal,
    TrustClass,
    can_grant,
    daily_report_due,
    effective_allowed,
    narrowed_value,
    next_step,
)

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
ALL_ACTIONS = frozenset(ActionKind)


def test_start_is_only_pause_and_test() -> None:
    assert {TrustClass.PAUSE_AD, TrustClass.START_TEST} == START_CLASSES
    # Рост лимита открыт всем сразу (решение владельца проекта 2026-11-10).
    assert effective_allowed(ALL_ACTIONS, START_CLASSES) == {
        ActionKind.PAUSE_AD,
        ActionKind.START_TEST,
        ActionKind.INCREASE_LIMIT,
    }


def test_owner_cannot_widen_beyond_what_was_granted() -> None:
    # Владелец кабинета не отметил рост лимита: самому нельзя, даже когда класс открыт.
    allowed = frozenset({ActionKind.PAUSE_AD})
    assert effective_allowed(allowed, START_CLASSES) == {ActionKind.PAUSE_AD}


def test_granted_class_still_needs_owner_tick() -> None:
    granted = START_CLASSES | {TrustClass.SHIFT_LIMIT}
    only_pause = frozenset({ActionKind.PAUSE_AD})
    assert effective_allowed(only_pause, granted) == {ActionKind.PAUSE_AD}


def test_ladder_goes_one_step_at_a_time() -> None:
    assert next_step(START_CLASSES) is TrustClass.SHIFT_LIMIT
    assert next_step(START_CLASSES | {TrustClass.SHIFT_LIMIT}) is TrustClass.SET_SCHEDULE
    assert next_step(frozenset(TrustClass)) is None


def test_grant_needs_next_step_a_clean_week_and_no_failures() -> None:
    week_ago = NOW - timedelta(days=7)
    assert can_grant(TrustClass.SHIFT_LIMIT, START_CLASSES, week_ago, 0, NOW) is None
    assert can_grant(TrustClass.SET_SCHEDULE, START_CLASSES, week_ago, 0, NOW) is (
        GrantRefusal.NOT_NEXT_STEP
    )
    assert can_grant(TrustClass.SHIFT_LIMIT, START_CLASSES, NOW - timedelta(days=6), 0, NOW) is (
        GrantRefusal.TOO_EARLY
    )
    assert can_grant(TrustClass.SHIFT_LIMIT, START_CLASSES, week_ago, 1, NOW) is (
        GrantRefusal.HAD_FAILURES
    )


def test_grant_without_known_start_is_refused() -> None:
    # Неизвестно, когда кабинет начал работать сам: неизвестность не открывает ступень.
    assert can_grant(TrustClass.SHIFT_LIMIT, START_CLASSES, None, 0, NOW) is (
        GrantRefusal.TOO_EARLY
    )


def test_already_granted_class_is_refused() -> None:
    assert can_grant(TrustClass.PAUSE_AD, START_CLASSES, NOW - timedelta(days=9), 0, NOW) is (
        GrantRefusal.ALREADY
    )


def test_envelope_only_narrows_never_widens() -> None:
    assert narrowed_value(3, {"max_changes_per_day": 1}, "max_changes_per_day") == 1
    assert narrowed_value(3, {"max_changes_per_day": 9}, "max_changes_per_day") == 3
    assert narrowed_value(3, {}, "max_changes_per_day") == 3
    assert narrowed_value(3, {"max_changes_per_day": 0}, "max_changes_per_day") == 0


def test_daily_report_runs_only_the_first_14_days_of_auto() -> None:
    assert daily_report_due(NOW - timedelta(days=3), NOW) is True
    assert daily_report_due(NOW - timedelta(days=13), NOW) is True
    assert daily_report_due(NOW - timedelta(days=14), NOW) is False
    assert daily_report_due(None, NOW) is False


def test_daily_report_counts_14_calendar_days_in_moscow() -> None:
    started = datetime(2026, 12, 1, 21, 30, tzinfo=UTC)  # 2 декабря 00:30 по Москве
    last_day = datetime(2026, 12, 15, 20, 59, tzinfo=UTC)  # 15 декабря 23:59 по Москве
    next_day = datetime(2026, 12, 15, 21, 0, tzinfo=UTC)  # 16 декабря 00:00 по Москве
    assert daily_report_due(started, last_day) is True
    assert daily_report_due(started, next_day) is False
    assert daily_report_due(None, last_day) is False
