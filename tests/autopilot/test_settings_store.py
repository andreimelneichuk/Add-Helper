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
