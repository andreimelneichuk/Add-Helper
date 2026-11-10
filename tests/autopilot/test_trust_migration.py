"""Миграция 0039: кабинеты, уже включённые в «Сам», получают начало отсчёта отчёта и ступеней."""

import uuid
from datetime import UTC, datetime

import psycopg
from alembic.command import downgrade, upgrade
from alembic.config import Config

from adhelper.config import Settings
from tests.conftest import ALEMBIC_INI


def _dsn(settings: Settings) -> str:
    return (
        f"postgresql://{settings.postgres_user}:"
        f"{settings.postgres_password.get_secret_value() or 'adhelper'}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


def test_auto_cabinet_gets_its_start_and_default_steps_and_migration_is_reversible(
    settings: Settings,
) -> None:
    config = Config(ALEMBIC_INI)
    upgrade(config, "0038")
    user, business = uuid.uuid4(), uuid.uuid4()
    started = datetime(2026, 12, 1, 6, 0, tzinfo=UTC)
    with psycopg.connect(_dsn(settings), autocommit=True) as connection:
        connection.execute("INSERT INTO users (id, yandex_uid) VALUES (%s, %s)", (user, str(user)))
        connection.execute(
            "INSERT INTO businesses (id, user_id, name) VALUES (%s, %s, 'Тест')", (business, user)
        )
        connection.execute(
            "INSERT INTO autopilot_settings (business_id, mode, allowed_actions, cadence,"
            " run_hour, envelope, updated_at) VALUES (%s, 'auto', '[\"pause_ad\"]'::jsonb,"
            " 'daily', 3, '{}'::jsonb, %s)",
            (business, started),
        )

    upgrade(config, "0039")

    with psycopg.connect(_dsn(settings)) as connection:
        row = connection.execute(
            "SELECT auto_since, granted_classes, granted_at FROM autopilot_settings"
        ).fetchone()
    assert row is not None
    assert row[0] == started and row[2] is None
    assert sorted(row[1]) == ["pause_ad", "start_test"]

    downgrade(config, "0038")
    upgrade(config, "head")
