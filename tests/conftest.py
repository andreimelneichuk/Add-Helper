import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic.command import upgrade
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.config import Settings
from adhelper.db import create_engine

ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"


def _admin_dsn(settings: Settings) -> str:
    url = make_url(settings.database_url).set(drivername="postgresql", database="postgres")
    return url.render_as_string(hide_password=False)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Настройки с отдельной временной базой: тесты не трогают рабочую."""
    name = f"adhelper_test_{uuid.uuid4().hex}"
    admin_dsn = _admin_dsn(Settings())
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    monkeypatch.setenv("POSTGRES_DB", name)
    yield Settings()
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def migrated(settings: Settings) -> Iterator[Settings]:
    upgrade(Config(ALEMBIC_INI), "head")
    yield settings


@pytest.fixture
async def engine(migrated: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
