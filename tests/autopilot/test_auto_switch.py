"""Включение «Сам» на кабинете: начало отсчёта, узкие рамки по умолчанию, выход из режима."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from adhelper.autopilot.application.settings import (
    AutoNotAllowed,
    AutopilotMode,
    AutopilotSettings,
    Patch,
    stop,
    update_settings,
)
from adhelper.autopilot.domain.settings import DEFAULT_SETTINGS
from adhelper.autopilot.domain.trust import FIRST_CABINET_ENVELOPE

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


class Store:
    def __init__(self, settings: AutopilotSettings | None = None) -> None:
        self.settings = settings
        self.lines: list[str] = []

    async def get(self) -> AutopilotSettings | None:
        return self.settings

    async def save(
        self,
        settings: AutopilotSettings,
        lines: Sequence[str],
        before: AutopilotSettings | None = None,
    ) -> None:
        self.settings = settings
        self.lines += lines

    async def demote_from_auto(self, line: str) -> bool:
        return False


async def test_auto_is_refused_while_the_project_owner_has_not_allowed_it() -> None:
    store = Store()
    with pytest.raises(AutoNotAllowed):
        await update_settings(store, Patch(mode=AutopilotMode.AUTO), auto_allowed=False, now=NOW)
    assert store.settings is None


async def test_first_auto_starts_the_clock_and_narrows_the_envelope() -> None:
    store = Store()

    after = await update_settings(store, Patch(mode=AutopilotMode.AUTO), auto_allowed=True, now=NOW)

    assert after.auto_since == NOW
    assert dict(after.envelope) == dict(FIRST_CABINET_ENVELOPE)
    assert any("узк" in line for line in store.lines)


async def test_owner_may_narrow_the_envelope_and_the_clock_is_not_restarted() -> None:
    mine = {"max_changes_per_day": 0}
    store = Store(replace(DEFAULT_SETTINGS, envelope=mine))
    await update_settings(store, Patch(mode=AutopilotMode.AUTO), auto_allowed=True, now=NOW)
    again = await update_settings(
        store, Patch(run_hour=8), auto_allowed=True, now=NOW + timedelta(days=3)
    )
    assert dict(again.envelope) == {"max_changes_per_day": 0, "max_weekly_move_bp": 500}
    assert again.auto_since == NOW


async def test_owner_cannot_widen_or_drop_the_default_envelope_in_auto() -> None:
    store = Store()
    await update_settings(store, Patch(mode=AutopilotMode.AUTO), auto_allowed=True, now=NOW)

    dropped = await update_settings(store, Patch(envelope={}), auto_allowed=True, now=NOW)
    widened = await update_settings(
        store,
        Patch(envelope={"max_changes_per_day": 9, "max_weekly_move_bp": 5000}),
        auto_allowed=True,
        now=NOW,
    )

    assert dict(dropped.envelope) == dict(FIRST_CABINET_ENVELOPE)
    assert dict(widened.envelope) == dict(FIRST_CABINET_ENVELOPE)


async def test_envelope_edit_while_already_in_auto_is_clamped_too() -> None:
    # Кабинет уже в «Сам» (например, включён до этой правки) и рамок нет: правка тоже сужается.
    store = Store(replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, auto_since=NOW, envelope={}))

    after = await update_settings(store, Patch(run_hour=8), auto_allowed=True, now=NOW)

    assert dict(after.envelope) == dict(FIRST_CABINET_ENVELOPE)


async def test_leaving_auto_clears_the_clock_and_coming_back_restarts_it() -> None:
    store = Store()
    await update_settings(store, Patch(mode=AutopilotMode.AUTO), auto_allowed=True, now=NOW)
    left = await update_settings(
        store, Patch(mode=AutopilotMode.CONFIRM), auto_allowed=True, now=NOW + timedelta(days=2)
    )
    assert left.auto_since is None
    back = await update_settings(
        store, Patch(mode=AutopilotMode.AUTO), auto_allowed=True, now=NOW + timedelta(days=5)
    )
    assert back.auto_since == NOW + timedelta(days=5)


async def test_header_stop_keeps_working_in_auto() -> None:
    store = Store()
    await update_settings(store, Patch(mode=AutopilotMode.AUTO), auto_allowed=True, now=NOW)
    stopped = await stop(store, NOW + timedelta(hours=1))
    assert stopped.stopped_at == NOW + timedelta(hours=1)
