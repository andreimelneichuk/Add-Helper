"""Настройки автопилота: значения, проверка, расписание."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.autopilot.domain.decide import ActionKind, Mode
from adhelper.autopilot.domain.settings import (
    DEFAULT_SETTINGS,
    MSK,
    AutopilotMode,
    AutopilotSettings,
    Cadence,
    SettingsError,
    cycle_mode,
    is_due,
    next_run_at,
    validated,
)

# 2026-11-20 09:00 UTC = 12:00 МСК: слот 06:20 МСК этого дня уже прошёл.
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
SLOT = datetime(2026, 11, 20, 6, 20, tzinfo=MSK)


def settings(**changes: object) -> AutopilotSettings:
    values: dict[str, object] = {"last_run_at": None} | changes
    return replace(DEFAULT_SETTINGS, **values)  # type: ignore[arg-type]


def test_defaults_match_the_plan() -> None:
    assert DEFAULT_SETTINGS.mode is AutopilotMode.CONFIRM
    assert DEFAULT_SETTINGS.allowed_actions == frozenset(
        {ActionKind.PAUSE_AD, ActionKind.START_TEST, ActionKind.INCREASE_LIMIT}
    )
    assert (DEFAULT_SETTINGS.cadence, DEFAULT_SETTINGS.run_hour) == (Cadence.DAILY, 6)
    assert DEFAULT_SETTINGS.stopped_at is None


def test_modes_map_to_cycle_modes() -> None:
    assert cycle_mode(AutopilotMode.WATCH, auto_allowed=True) is Mode.PAUSE
    assert cycle_mode(AutopilotMode.CONFIRM, auto_allowed=True) is Mode.CONFIRM
    assert cycle_mode(AutopilotMode.AUTO, auto_allowed=True) is Mode.AUTO


def test_auto_falls_back_to_confirm_while_not_allowed() -> None:
    assert cycle_mode(AutopilotMode.AUTO, auto_allowed=False) is Mode.CONFIRM


@pytest.mark.parametrize("hour", [-1, 24, 100])
def test_bad_run_hour_is_refused(hour: int) -> None:
    with pytest.raises(SettingsError):
        validated(settings(run_hour=hour))


def test_empty_allowed_actions_are_refused() -> None:
    with pytest.raises(SettingsError):
        validated(settings(allowed_actions=frozenset()))


def test_envelope_must_hold_whole_numbers_only() -> None:
    validated(settings(envelope={"max_daily_change_bp": 2000}))
    for bad in ({"x": 1.5}, {"x": "1"}, {"x": True}, {"x": -1}):
        with pytest.raises(SettingsError):
            validated(settings(envelope=bad))


def test_never_run_waits_for_todays_slot() -> None:
    assert is_due(settings(), NOW)
    assert not is_due(settings(), datetime(2026, 11, 20, 2, 0, tzinfo=UTC))  # 05:00 МСК
    assert next_run_at(settings(), datetime(2026, 11, 20, 2, 0, tzinfo=UTC)) == datetime(
        2026, 11, 20, 3, 20, tzinfo=UTC
    )


def test_daily_runs_once_per_slot() -> None:
    ran = settings(last_run_at=NOW)
    assert not is_due(ran, NOW + timedelta(hours=5))
    assert is_due(ran, datetime(2026, 11, 21, 3, 20, tzinfo=UTC))  # следующий слот 06:20 МСК
    assert not is_due(ran, datetime(2026, 11, 21, 3, 19, tzinfo=UTC))


def test_stopped_is_never_due() -> None:
    assert not is_due(settings(stopped_at=NOW), NOW + timedelta(days=30))


def test_next_run_is_the_next_slot_after_a_run() -> None:
    assert next_run_at(settings(last_run_at=NOW), NOW) == datetime(2026, 11, 21, 3, 20, tzinfo=UTC)
    assert next_run_at(settings(stopped_at=NOW), NOW) is None


@given(st.integers(min_value=0, max_value=7 * 24 * 60 - 1))
def test_weekly_never_runs_before_seven_days(minutes_after: int) -> None:
    weekly = settings(cadence=Cadence.WEEKLY, last_run_at=NOW)
    now = NOW + timedelta(minutes=minutes_after)
    if (now.astimezone(MSK).date() - NOW.astimezone(MSK).date()).days < 7:
        assert not is_due(weekly, now)


def test_weekly_runs_on_the_seventh_day_slot() -> None:
    weekly = settings(cadence=Cadence.WEEKLY, last_run_at=NOW)
    assert is_due(weekly, datetime(2026, 11, 27, 3, 20, tzinfo=UTC))
    assert next_run_at(weekly, NOW) == datetime(2026, 11, 27, 3, 20, tzinfo=UTC)


@given(
    st.integers(min_value=0, max_value=23),
    st.sampled_from(list(Cadence)),
    st.integers(min_value=0, max_value=60 * 24 * 40),
)
def test_a_run_makes_the_same_moment_not_due(hour: int, cadence: Cadence, offset: int) -> None:
    now = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=offset)
    ran = settings(run_hour=hour, cadence=cadence, last_run_at=now)
    assert not is_due(ran, now)
    following = next_run_at(ran, now)
    assert following is not None and following > now
    assert is_due(ran, following)


class MemoryStore:
    def __init__(self, current: AutopilotSettings | None) -> None:
        self.current = current
        self.lines: list[str] = []

    async def get(self) -> AutopilotSettings | None:
        return self.current

    async def save(
        self,
        settings: AutopilotSettings,
        lines: Sequence[str],
        before: AutopilotSettings | None = None,
    ) -> None:
        self.current = settings
        self.lines.extend(lines)

    async def demote_from_auto(self, line: str) -> bool:
        if self.current is None or self.current.mode is not AutopilotMode.AUTO:
            return False
        self.current = replace(self.current, mode=AutopilotMode.CONFIRM)
        self.lines.append(line)
        return True


async def test_demote_moves_auto_to_confirm_and_writes_the_reason() -> None:
    from adhelper.autopilot.application.settings import demote

    store = MemoryStore(settings(mode=AutopilotMode.AUTO))
    assert await demote(store, "за сутки уже 2 автоматических отката") is True
    assert store.current is not None and store.current.mode is AutopilotMode.CONFIRM
    assert store.lines == ["Выключил самостоятельный режим: за сутки уже 2 автоматических отката"]


async def test_demote_does_nothing_outside_auto() -> None:
    from adhelper.autopilot.application.settings import demote

    for mode in (AutopilotMode.WATCH, AutopilotMode.CONFIRM):
        store = MemoryStore(settings(mode=mode))
        assert await demote(store, "причина") is False
        assert store.lines == [] and store.current is not None and store.current.mode is mode
