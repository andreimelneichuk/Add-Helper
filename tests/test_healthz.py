from pathlib import Path

import httpx
import psycopg
from alembic.command import check, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from adhelper.api.app import create_app
from adhelper.config import Settings

ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"


async def _get_healthz(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/healthz")
    finally:
        await app.state.engine.dispose()


def test_migrations_reach_head_on_real_postgres(settings: Settings) -> None:
    config = Config(ALEMBIC_INI)

    upgrade(config, "head")

    dsn = make_url(settings.database_url).set(drivername="postgresql")
    with psycopg.connect(dsn.render_as_string(hide_password=False)) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row == (ScriptDirectory.from_config(config).get_current_head(),)


async def test_healthz_ok_when_database_is_available(settings: Settings) -> None:
    response = await _get_healthz(create_app(settings))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_unavailable_when_database_is_down(settings: Settings) -> None:
    down = settings.model_copy(update={"postgres_port": 1})

    response = await _get_healthz(create_app(down))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_models_match_migrations(settings: Settings) -> None:
    config = Config(ALEMBIC_INI)
    upgrade(config, "head")

    check(config)
