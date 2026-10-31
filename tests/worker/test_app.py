"""Настоящий воркер Procrastinate: задания из базы выполняются и не теряются."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from adhelper.config import Settings
from adhelper.worker.app import PERIODIC_TASKS, app, budget_watch, connector_for, state_reconcile


def test_money_tasks_are_scheduled() -> None:
    assert {"budget.watch", "state.reconcile"} <= set(PERIODIC_TASKS)
    assert set(app.tasks) >= {"budget.watch", "state.reconcile"}
    scheduled = {periodic.task.name for periodic in app.periodic_registry.periodic_tasks.values()}
    assert {"budget.watch", "state.reconcile"} <= scheduled


async def test_worker_runs_money_tasks_from_real_database(
    migrated: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_DB", migrated.postgres_db)
    periodic_arguments: dict[str, Any] = {"timestamp": 1}

    with app.replace_connector(connector_for(migrated)) as test_app:
        async with test_app.open_async():
            await budget_watch.defer_async(**periodic_arguments)
            await state_reconcile.defer_async(**periodic_arguments)
            await test_app.run_worker_async(
                queues=["budget"], wait=False, install_signal_handlers=False
            )

    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT task_name, status FROM procrastinate_jobs WHERE scheduled_at IS NULL")
        )
        statuses = {name: str(status) for name, status in rows}
    assert statuses == {"budget.watch": "succeeded", "state.reconcile": "succeeded"}
