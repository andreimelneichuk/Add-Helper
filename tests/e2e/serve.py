"""Стенд сквозных тестов: своя база, демо-кабинет и поддельная площадка в памяти.

Поддельная площадка подключается только отсюда, в рабочем коде её нет. Так же здесь
воркеры разбора сайта, проверки кода Метрики и писателя объявлений: тот же код очередей,
но сайт и модель поддельные.
"""

import asyncio
import json
import os
import secrets
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from fastapi import APIRouter
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from adhelper.accounts.application.service import hash_token
from adhelper.budget.domain.limits import CampaignBudget
from adhelper.business.adapters.rules_file import load_site_rules, load_tag_rules
from adhelper.business.adapters.site_reads import SqlSiteReadQueue
from adhelper.business.adapters.tag_checks import SqlTagCheckQueue
from adhelper.business.application.site import run_site_reads
from adhelper.business.application.tag import run_tag_checks
from adhelper.business.domain.ports import FetchedPage
from adhelper.config import Settings
from adhelper.creatives.adapters.rules_file import load_rework_rules, load_rules
from adhelper.creatives.adapters.sql import SqlCreativeQueue, SqlProfileSource
from adhelper.creatives.application.service import run_queued
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
        # Код Метрики на странице: шаг «Статистика» находит счётчик.
        return FetchedPage(url, SITE_TEXT, (), (98765432,))


# Объявления писателя по фактам сайта стенда: проходят проверку кодом.
STAND_ADS = [
    {
        "headline": "Маникюр с покрытием гель-лаком",
        "text": "Маникюр с покрытием гель-лаком за 1700 ₽ на Тверской, 7.",
    },
    {"headline": "Маникюр на Тверской", "text": "Запишитесь на маникюр: Москва, ул. Тверская, 7."},
]


def _rework_ads(prompt: str) -> list[dict[str, str]]:
    """Варианты переделки строим из профиля в запросе: так они проходят проверку фактов."""
    profile = json.loads(prompt.split("ПРОФИЛЬ:\n", 1)[1])
    first = profile["услуги"][0]
    price = f" за {first['цена_руб']} ₽" if first["цена_руб"] else ""
    feature = (profile["особенности"] or [profile["адрес"]])[0]
    return [
        {"headline": f"{first['услуга']}{price}", "text": f"Ждём вас: {profile['адрес']}."},
        {"headline": first["услуга"], "text": f"{feature}."},
    ]


class StandModel:
    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        if "ПОЖЕЛАНИЕ" in user:
            return ModelReply(json.dumps({"ads": _rework_ads(user)}, ensure_ascii=False), 100)
        if "ads" in schema.get("properties", {}):  # type: ignore[operator]
            return ModelReply(json.dumps({"ads": STAND_ADS}, ensure_ascii=False), 100)
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
            await run_queued(
                SqlCreativeQueue(app.state.sessions),
                SqlProfileSource(app.state.sessions),
                StandModel(),
                None,
                load_rules,
                lambda: datetime.now(UTC),
                load_rework=load_rework_rules,
            )
            await run_tag_checks(
                SqlTagCheckQueue(app.state.sessions),
                StandSite(),
                load_tag_rules(),
                lambda: datetime.now(UTC),
            )
            await asyncio.sleep(1)

    task = asyncio.create_task(site_reads())
    try:
        await server.serve()
    finally:
        task.cancel()


# Логин кабинета, который стенд подключает новому владельцу вместо входа в Яндекс.
STAND_LOGIN = "stand-direct"


def _stand_router(app: Any) -> APIRouter:
    """Только для стенда: новый владелец без бизнеса и подключение кабинета без Яндекса."""
    router = APIRouter(prefix="/api/stand")

    @router.post("/owners")
    async def new_owner() -> dict[str, str]:
        user_id, token = uuid.uuid4(), secrets.token_urlsafe(32)
        async with app.state.sessions.begin() as session:
            await session.execute(
                text("INSERT INTO users (id, yandex_uid, email) VALUES (:id, :uid, :email)"),
                {"id": user_id, "uid": f"stand-{user_id}", "email": "owner@example.ru"},
            )
            await session.execute(
                text(
                    "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
                    " VALUES (:h, :u, :c, :e)"
                ),
                {
                    "h": hash_token(token),
                    "u": user_id,
                    "c": secrets.token_urlsafe(32),
                    "e": datetime.now(UTC) + timedelta(days=1),
                },
            )
        return {"cookie": token}

    @router.post("/businesses/{business_id}/connect")
    async def connect(business_id: uuid.UUID) -> dict[str, str]:
        async with app.state.sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO ad_accounts (id, business_id, platform, external_login,"
                    " encrypted_access, encrypted_refresh, token_expires_at, scopes, state)"
                    " VALUES (:id, :b, 'yandex_direct', :login, 'x', 'x', :e, '{}', 'connected')"
                ),
                {
                    "id": uuid.uuid4(),
                    "b": business_id,
                    "login": f"{STAND_LOGIN}-{str(business_id)[:8]}",
                    "e": datetime.now(UTC) + timedelta(days=30),
                },
            )
        return {"status": "connected"}

    return router


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
    demo = _platform()
    # У каждого кабинета своя площадка: кампании демо-кабинета не считаются в бюджет нового.
    platforms: dict[str, FakePlatform] = {}

    def platform_for(_business: object, account: str) -> FakePlatform:
        if not account.startswith(STAND_LOGIN):
            return demo
        return platforms.setdefault(account, FakePlatform([], datetime.now(UTC)))

    app.state.platform_for = platform_for
    app.include_router(_stand_router(app))
    asyncio.run(_serve(app))


if __name__ == "__main__":
    main()
