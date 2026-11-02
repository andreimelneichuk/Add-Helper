"""Стенд сквозных тестов: своя база, демо-кабинет и поддельная площадка в памяти.

Поддельная площадка подключается только отсюда, в рабочем коде её нет.
"""

import asyncio
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import uvicorn
from psycopg import sql
from sqlalchemy.engine import make_url

from adhelper.budget.domain.limits import CampaignBudget
from adhelper.config import Settings
from adhelper.worker.demo import ADS, CAMPAIGNS, ENV_FILE, seed_demo, write_demo_env
from adhelper.worker.runtime import open_runtime
from tests.budget.fake_platform import FakePlatform

DATABASE = "adhelper_e2e"
PORT = 8011
STATE = Path(__file__).parent / ".state"


def _recreate_database() -> None:
    url = make_url(Settings().database_url).set(drivername="postgresql", database="postgres")
    dsn = url.render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(DATABASE))
        )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))


def _load_env() -> None:
    for line in (STATE / ENV_FILE).read_text().splitlines():
        key, _, value = line.partition("=")
        os.environ[key] = value


async def _seed() -> None:
    settings = Settings()
    async with open_runtime(settings) as runtime:
        await seed_demo(runtime.sessions, runtime.clock(), STATE, False)


def _platform() -> FakePlatform:
    campaigns = [
        CampaignBudget(row[0], row[3] * 1_000_000, 0, row[2] == "on", None) for row in CAMPAIGNS
    ]
    fake = FakePlatform(campaigns, datetime.now(UTC))
    fake.ads = {f"ad:{ad[0]}": ad[3] == "on" for lines in ADS.values() for ad in lines}
    return fake


def main() -> None:
    STATE.mkdir(exist_ok=True)
    os.environ["POSTGRES_DB"] = DATABASE
    _recreate_database()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    write_demo_env(STATE)
    _load_env()
    asyncio.run(_seed())

    from adhelper.api.app import create_app

    app = create_app()
    platform = _platform()
    app.state.platform_for = lambda _business, _account: platform
    uvicorn.run(app, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
