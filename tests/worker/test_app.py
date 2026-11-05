"""Настоящий воркер Procrastinate: задания из базы выполняются и не теряются."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from adhelper.config import Settings
from adhelper.worker.app import (
    PERIODIC_TASKS,
    ads_keep,
    app,
    budget_watch,
    connector_for,
    creatives_generate,
    notifications_send,
    site_read,
    site_tag_check,
    state_reconcile,
    telegram_poll,
)


def test_money_tasks_are_scheduled() -> None:
    expected = {
        "budget.watch",
        "state.reconcile",
        "creatives.generate",
        "ads.keep",
        "ads.moderate",
        "notifications.send",
        "telegram.poll",
        "site.read",
        "site.tag_check",
        "experiments.run",
        "experiments.stats",
        "ads.summary",
    }
    assert expected <= set(PERIODIC_TASKS)
    assert set(app.tasks) >= expected
    scheduled = {periodic.task.name for periodic in app.periodic_registry.periodic_tasks.values()}
    assert expected <= scheduled


def test_compose_worker_listens_to_every_queue() -> None:
    compose = (Path(__file__).parents[2] / "compose.yaml").read_text()
    assert "--queues=budget,creatives,notifications" in compose


async def test_worker_runs_money_tasks_from_real_database(
    migrated: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_DB", migrated.postgres_db)
    periodic_arguments: dict[str, Any] = {"timestamp": 1}

    with app.replace_connector(connector_for(migrated)) as test_app:
        async with test_app.open_async():
            await budget_watch.defer_async(**periodic_arguments)
            await state_reconcile.defer_async(**periodic_arguments)
            await creatives_generate.defer_async(**periodic_arguments)
            await ads_keep.defer_async(**periodic_arguments)
            await notifications_send.defer_async(**periodic_arguments)
            await telegram_poll.defer_async(**periodic_arguments)
            await site_read.defer_async(**periodic_arguments)
            await site_tag_check.defer_async(**periodic_arguments)
            await test_app.run_worker_async(
                queues=["budget", "creatives", "notifications"],
                wait=False,
                install_signal_handlers=False,
            )

    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT task_name, status FROM procrastinate_jobs WHERE scheduled_at IS NULL")
        )
        statuses = {name: str(status) for name, status in rows}
    assert statuses == {
        "budget.watch": "succeeded",
        "state.reconcile": "succeeded",
        "creatives.generate": "succeeded",
        "ads.keep": "succeeded",
        "ads.moderate": "succeeded",
        "notifications.send": "succeeded",
        "telegram.poll": "succeeded",
        "site.read": "succeeded",
        "site.tag_check": "succeeded",
        "experiments.run": "succeeded",
    }
