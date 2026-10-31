import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from adhelper.config import Settings


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
