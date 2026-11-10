"""Снижение «Сам» → «Предлагает» одним условным запросом: без чтения и перезаписи настроек."""

import asyncio
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.settings import SqlAutopilotSettings
from adhelper.autopilot.domain.settings import DEFAULT_SETTINGS, AutopilotMode, Cadence
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
async def _owner(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


async def test_concurrent_demotions_change_the_mode_once(sessions: Sessions) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    await store.save(replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, cadence=Cadence.WEEKLY), [])

    results = await asyncio.gather(
        store.demote_from_auto("Выключил самостоятельный режим: причина"),
        store.demote_from_auto("Выключил самостоятельный режим: причина"),
    )

    assert sorted(results) == [False, True]
    current = await store.get()
    assert current is not None and current.mode is AutopilotMode.CONFIRM
    # Остальные настройки не затронуты: чтения и перезаписи целой строки нет.
    assert current.cadence is Cadence.WEEKLY
    lines = [line.text for line in await store.history()]
    assert lines == ["Выключил самостоятельный режим: причина"]


async def test_demotion_outside_auto_changes_nothing(sessions: Sessions) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    assert await store.demote_from_auto("причина") is False  # настроек ещё нет
    await store.save(replace(DEFAULT_SETTINGS, mode=AutopilotMode.CONFIRM), [])

    assert await store.demote_from_auto("причина") is False

    async with sessions() as session:
        count = await session.scalar(text("SELECT count(*) FROM autopilot_settings_log"))
    assert count == 0


async def test_stale_edit_does_not_bring_back_a_revoked_step(sessions: Sessions) -> None:
    from datetime import UTC, datetime

    from adhelper.autopilot.domain.trust import START_CLASSES, TrustClass

    store = SqlAutopilotSettings(sessions, BUSINESS)
    opened = START_CLASSES | {TrustClass.SHIFT_LIMIT}
    await store.save(replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO, granted=opened), [])
    stale = await store.get()  # правка владельца кабинета прочитала настройки
    assert stale is not None
    # Пока она готовилась, владелец проекта закрыл ступень.
    await store.save(replace(stale, granted=START_CLASSES, granted_at=datetime.now(UTC)), [],
                     before=stale)  # fmt: skip

    await store.save(replace(stale, cadence=Cadence.WEEKLY), ["Автопилот раз в неделю"],
                     before=stale)  # fmt: skip

    current = await store.get()
    assert current is not None and current.cadence is Cadence.WEEKLY
    assert current.granted == START_CLASSES and current.granted_at is not None


async def test_stale_edit_does_not_bring_back_auto_after_a_demotion(sessions: Sessions) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)
    await store.save(replace(DEFAULT_SETTINGS, mode=AutopilotMode.AUTO), [])
    stale = await store.get()
    assert stale is not None
    assert await store.demote_from_auto("Выключил самостоятельный режим: срыв")

    await store.save(replace(stale, run_hour=5), ["Автопилот разбирает рекламу в 5:20 по Москве"],
                     before=stale)  # fmt: skip

    current = await store.get()
    assert current is not None and current.run_hour == 5
    assert current.mode is AutopilotMode.CONFIRM and current.auto_since is None


async def test_first_save_without_a_row_writes_everything(sessions: Sessions) -> None:
    store = SqlAutopilotSettings(sessions, BUSINESS)

    await store.save(replace(DEFAULT_SETTINGS, run_hour=7), ["x"], before=DEFAULT_SETTINGS)

    current = await store.get()
    assert (
        current is not None
        and current.run_hour == 7
        and current.granted == (DEFAULT_SETTINGS.granted)
    )
