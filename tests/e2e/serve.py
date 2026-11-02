"""Стенд сквозных тестов: своя база, демо-кабинет и поддельная площадка в памяти.

Поддельная площадка подключается только отсюда, в рабочем коде её нет. Так же здесь
воркер разбора сайта: тот же код очереди, но сайт и модель поддельные.
"""

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from psycopg import sql
from sqlalchemy.engine import make_url

from adhelper.budget.domain.limits import CampaignBudget
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.adapters.site_reads import SqlSiteReadQueue
from adhelper.business.application.site import run_site_reads
from adhelper.business.domain.ports import FetchedPage
from adhelper.config import Settings
from adhelper.creatives.domain.model import ModelReply
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


SITE_TEXT = (
    "Студия маникюра «Ноготок»\nМаникюр с покрытием гель-лаком — 1 700 ₽\n"
    "Адрес: Москва, ул. Тверская, 7\nРаботаем с 2015 года"
)
SITE_FACTS = [
    ("niche", "маникюр", "Студия маникюра «Ноготок»", None),
    ("service", "Маникюр с покрытием гель-лаком", "Маникюр с покрытием гель-лаком — 1 700 ₽", 1700),
    ("address", "Москва, ул. Тверская, 7", "Адрес: Москва, ул. Тверская, 7", None),
    # Цитаты нет на странице: код должен её отбросить и владелец её не увидит.
    ("feature", "Лучшие мастера города", "Лучшие мастера города", None),
]


class StandSite:
    async def fetch(self, url: str) -> FetchedPage:
        return FetchedPage(url, SITE_TEXT)


class StandModel:
    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        keys = ("field", "value", "quote", "price")
        facts = [dict(zip(keys, fact, strict=True)) for fact in SITE_FACTS]
        return ModelReply(json.dumps({"facts": facts}, ensure_ascii=False), 100)


async def _serve(app: Any) -> None:
    server = uvicorn.Server(uvicorn.Config(app, port=PORT, log_level="warning"))
    rules = load_site_rules()

    async def site_reads() -> None:
        while True:
            await run_site_reads(
                SqlSiteReadQueue(app.state.sessions),
                StandSite(),
                StandModel(),
                "stand",
                rules,
                load_site_rules,
                lambda: datetime.now(UTC),
            )
            await asyncio.sleep(1)

    task = asyncio.create_task(site_reads())
    try:
        await server.serve()
    finally:
        task.cancel()


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
    asyncio.run(_serve(app))


if __name__ == "__main__":
    main()
