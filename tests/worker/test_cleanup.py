"""Проверка автоматической очистки старых задач Procrastinate и сердцебиения воркера."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from adhelper.config import Settings
from adhelper.worker.runtime import open_runtime


async def _insert_job(
    engine: AsyncEngine,
    task_name: str,
    status: str,
    event_age: timedelta,
) -> int:
    event_time = datetime.now(UTC) - event_age
    async with engine.begin() as conn:
        job_id = await conn.scalar(
            text(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, status, scheduled_at) "
                "VALUES ('test_queue', :task, CAST(:status AS procrastinate_job_status), :sched) "
                "RETURNING id"
            ),
            {"task": task_name, "status": status, "sched": event_time},
        )
        await conn.execute(
            text(
                "INSERT INTO procrastinate_events (job_id, type, at) "
                "VALUES (:job_id, CAST('succeeded' AS procrastinate_job_event_type), :at)"
            ),
            {"job_id": job_id, "at": event_time},
        )
        return int(job_id)


async def test_cleanup_jobs_removes_old_finished_jobs_only(
    migrated: Settings, engine: AsyncEngine
) -> None:
    old = timedelta(days=8)
    recent = timedelta(days=2)

    # Старые выполненные и отмененные — должны быть удалены
    id_succeeded_old = await _insert_job(engine, "task.old.succ", "succeeded", old)
    id_cancelled_old = await _insert_job(engine, "task.old.canc", "cancelled", old)

    # Недавние выполненные — должны остаться
    id_succeeded_recent = await _insert_job(engine, "task.recent.succ", "succeeded", recent)

    # Активные или упавшие (даже старые) — должны остаться
    id_todo_old = await _insert_job(engine, "task.old.todo", "todo", old)
    id_doing_old = await _insert_job(engine, "task.old.doing", "doing", old)
    id_failed_old = await _insert_job(engine, "task.old.failed", "failed", old)

    async with open_runtime(migrated) as runtime:
        deleted = await runtime.cleanup_jobs(older_than_days=7)

    assert deleted >= 2

    async with engine.connect() as conn:
        remaining_ids = set(
            (await conn.execute(text("SELECT id FROM procrastinate_jobs"))).scalars().all()
        )
        events_job_ids = set(
            (await conn.execute(text("SELECT job_id FROM procrastinate_events"))).scalars().all()
        )

    assert id_succeeded_old not in remaining_ids
    assert id_cancelled_old not in remaining_ids
    assert id_succeeded_old not in events_job_ids
    assert id_cancelled_old not in events_job_ids

    assert id_succeeded_recent in remaining_ids
    assert id_todo_old in remaining_ids
    assert id_doing_old in remaining_ids
    assert id_failed_old in remaining_ids


async def test_worker_heartbeat_writes_and_updates_row(
    migrated: Settings, engine: AsyncEngine
) -> None:
    worker_name = "test_queue_worker"

    async with open_runtime(migrated) as runtime:
        await runtime.worker_heartbeat(worker_name)

    async with engine.connect() as conn:
        row1 = (
            await conn.execute(
                text("SELECT last_heartbeat FROM worker_heartbeats WHERE worker_name = :name"),
                {"name": worker_name},
            )
        ).scalar_one_or_none()
        assert row1 is not None

    async with open_runtime(migrated) as runtime:
        await runtime.worker_heartbeat(worker_name)

    async with engine.connect() as conn:
        row2 = (
            await conn.execute(
                text("SELECT last_heartbeat FROM worker_heartbeats WHERE worker_name = :name"),
                {"name": worker_name},
            )
        ).scalar_one_or_none()
        assert row2 is not None
        assert row2 >= row1
