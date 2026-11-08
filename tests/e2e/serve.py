"""Стенд сквозных тестов: своя база, демо-кабинет и поддельная площадка в памяти.

Поддельная площадка подключается только отсюда, в рабочем коде её нет. Так же здесь
воркеры разбора сайта, проверки кода Метрики и писателя объявлений: тот же код очередей,
но сайт и модель поддельные.
"""

import asyncio
import base64
import dataclasses
import hashlib
import json
import os
import secrets
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import psycopg
import uvicorn
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
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
from adhelper.platforms.domain.ports import Advertiser
from adhelper.worker.demo import ADS, CAMPAIGNS, DEMO_LOGIN, ENV_FILE, seed_demo, write_demo_env
from adhelper.worker.runtime import Runtime, open_runtime
from tests.budget.fake_platform import FakePlatform
from tests.claims import with_claims

DATABASE = "adhelper_e2e"
PORT = 8011
STATE = Path(__file__).parent / ".state"


def _recreate_database() -> None:
    url = make_url(Settings().database_url).set(drivername="postgresql", database="postgres")
    dsn = url.render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = {}"
            ).format(sql.Literal(DATABASE))
        )
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
        "text": "Маникюр с покрытием гель-лаком за 1700 ₽. Москва, ул. Тверская, 7.",
    },
    {"headline": "Маникюр на Тверской", "text": "Запишитесь на маникюр: Москва, ул. Тверская, 7."},
]


def _rework_ads(prompt: str) -> list[dict[str, str]]:
    """Варианты переделки строим из профиля в запросе: так они проходят проверку фактов."""
    facts = json.loads(prompt.rsplit("ФАКТЫ:\n", 1)[1])
    price = facts.get("s1_price", "").rpartition(": ")[2]
    priced = f" за {price}" if price else ""
    feature = facts.get("f1") or facts.get("address", "")
    return [
        {"headline": f"{facts['s1']}{priced}", "text": f"Ждём вас: {facts.get('address', '')}."},
        {"headline": facts["s1"], "text": f"{feature}."},
    ]


class StandModel:
    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        if "ПОЖЕЛАНИЕ" in user:
            reply = json.dumps({"ads": _rework_ads(user)}, ensure_ascii=False)
            return ModelReply(with_claims(reply, user), 100)
        if "ads" in schema.get("properties", {}):  # type: ignore[operator]
            reply = json.dumps({"ads": STAND_ADS}, ensure_ascii=False)
            return ModelReply(with_claims(reply, user), 100)
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
# Подставной Яндекс ID живёт в том же процессе: адреса задаёт конфигурация, как у заказчика.
YANDEX_PATH = "/stand-yandex"
YANDEX_SECRET = secrets.token_urlsafe(24)


def _yandex_router() -> APIRouter:
    """Строгий Яндекс ID для стенда: секрет приложения, PKCE S256, одноразовый код.

    Права `direct:api` дают кабинет `stand-direct-N`, без них — нового владельца.
    `POST …/deny` — следующий вход владелец отменит в Яндексе.
    """
    router = APIRouter(prefix=YANDEX_PATH)
    codes: dict[str, tuple[str, dict[str, str]]] = {}
    tokens: dict[str, dict[str, str]] = {}
    deny = {"next": False}
    counter = {"n": 0}

    def person(scope: str) -> dict[str, str]:
        counter["n"] += 1
        n = counter["n"]
        if "direct:api" in scope.split():
            return {"id": f"stand-cabinet-{n}", "login": f"{STAND_LOGIN}-{n:04d}"}
        return {
            "id": f"stand-owner-{n}",
            "login": f"owner-{n}",
            "default_email": "owner@example.ru",
        }

    @router.get("/authorize")
    async def authorize(
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str = "",
    ) -> RedirectResponse:
        valid_clients = {
            "stand-app",
            os.environ.get("YANDEX_CLIENT_ID"),
            os.environ.get("YANDEX_DIRECT_CLIENT_ID"),
        }
        if client_id not in valid_clients or code_challenge_method != "S256":
            raise HTTPException(400, "bad request")
        if deny["next"]:
            deny["next"] = False
            return RedirectResponse(
                f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}"
            )
        code = secrets.token_urlsafe(16)
        codes[code] = (code_challenge, person(scope))
        return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}")

    def issue(who: dict[str, str]) -> JSONResponse:
        access, refresh = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        tokens[access] = tokens[refresh] = who
        return JSONResponse(
            {"access_token": access, "refresh_token": refresh, "expires_in": 31536000}
        )

    @router.post("/token")
    async def token(request: Request) -> JSONResponse:
        # Форма без python-multipart: тело `application/x-www-form-urlencoded`.
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        valid_clients = {
            "stand-app",
            os.environ.get("YANDEX_CLIENT_ID"),
            os.environ.get("YANDEX_DIRECT_CLIENT_ID"),
        }
        if form.get("client_id") not in valid_clients or form.get("client_secret") != YANDEX_SECRET:
            return JSONResponse({"error": "invalid_client"}, 401)
        if form.get("grant_type") == "refresh_token":
            who = tokens.get(str(form.get("refresh_token")))
            return issue(who) if who else JSONResponse({"error": "invalid_grant"}, 400)
        found = codes.pop(str(form.get("code")), None)
        verifier = str(form.get("code_verifier", ""))
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if found is None or found[0] != challenge:
            return JSONResponse({"error": "invalid_grant"}, 400)
        return issue(found[1])

    @router.get("/info")
    async def info(request: Request) -> JSONResponse:
        header = request.headers.get("Authorization", "")
        who = tokens.get(header.removeprefix("OAuth "))
        return JSONResponse(who) if who else JSONResponse({}, 401)

    @router.post("/deny")
    async def deny_next() -> dict[str, bool]:
        deny["next"] = True
        return {"deny": True}

    return router


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

    @router.post("/businesses/{business_id}/autopilot")
    async def autopilot(business_id: uuid.UUID) -> dict[str, int]:
        """Суточный цикл автопилота сейчас, а не в 06:20: тот же код, что в воркере."""
        runtime = Runtime(
            sessions=app.state.sessions,
            engine=app.state.engine,
            platform_for=app.state.platform_for,
        )
        return {"accounts": await runtime.autopilot_run(only=business_id)}

    @router.post("/businesses/{business_id}/reset-sets")
    async def reset_sets(business_id: uuid.UUID) -> dict[str, str]:
        """Сброс суточного лимита наборов для непрерывных e2e тестов."""
        yesterday = datetime.now(UTC) - timedelta(days=2)
        async with app.state.sessions.begin() as session:
            await session.execute(
                text("UPDATE creative_sets SET created_at = :y WHERE business_id = :b"),
                {"y": yesterday, "b": business_id},
            )
        return {"status": "ok"}

    @router.post("/creatives/restore-needs-owner")
    async def restore_needs_owner() -> dict[str, str]:
        """Восстановление креатива в needs_owner для повторных e2e проверок."""
        async with app.state.sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE creatives SET state = 'needs_owner'"
                    " WHERE headline = 'Педикюр в студии «Лак»'"
                ),
            )
        return {"status": "ok"}

    @router.post("/businesses/{business_id}/verdicts")
    async def verdicts(business_id: uuid.UUID, body: dict[str, Any]) -> dict[str, int]:
        """Итог проверки Яндекса для объявлений «на проверке», чей заголовок содержит `headline`.

        Подставная площадка отвечает этим итогом, пока его не сменят: так ведёт себя Директ.
        """
        async with app.state.sessions() as session:
            account = await session.scalar(
                text("SELECT external_login FROM ad_accounts WHERE business_id = :b"),
                {"b": business_id},
            )
        fake = app.state.platform_for(business_id, account)
        async with app.state.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT ad_id FROM ads WHERE business_id = :b AND account = :a"
                        " AND state = 'moderation' AND title LIKE :h"
                    ),
                    {"b": business_id, "a": account, "h": f"%{body['headline']}%"},
                )
            ).all()
        for (ad_id,) in rows:
            if body["state"] == "accepted":
                fake.accept(ad_id)
            else:
                fake.reject(ad_id, *body.get("reasons", []))
        return {"ads": len(rows)}

    @router.post("/businesses/{business_id}/moderate")
    async def moderate(business_id: uuid.UUID) -> dict[str, str]:
        """Цикл отправки и проверки объявлений сейчас, а не раз в 5 минут: код воркера."""
        runtime = Runtime(
            sessions=app.state.sessions,
            engine=app.state.engine,
            platform_for=app.state.platform_for,
        )
        await runtime.ads_moderate(only=business_id)
        return {"status": "done"}

    @router.post("/reset-limits")
    async def reset_limits() -> dict[str, str]:
        """Сброс счётчиков разбора сайтов между тестами."""
        async with app.state.sessions() as session:
            await session.execute(text("DELETE FROM guest_site_reads"))
            await session.commit()
        return {"status": "ok"}

    return router


def main() -> None:
    STATE.mkdir(exist_ok=True)
    os.environ["POSTGRES_DB"] = DATABASE
    # Вход и подключение кабинета идут через подставной Яндекс ID стенда, а не подменой в тесте.
    os.environ["YANDEX_CLIENT_ID"] = "stand-app"
    os.environ["YANDEX_CLIENT_SECRET"] = YANDEX_SECRET
    os.environ["YANDEX_DIRECT_CLIENT_ID"] = "stand-app"
    os.environ["YANDEX_DIRECT_CLIENT_SECRET"] = YANDEX_SECRET
    os.environ["YANDEX_OAUTH_URL"] = f"http://localhost:{PORT}{YANDEX_PATH}"
    os.environ["YANDEX_LOGIN_URL"] = f"http://localhost:{PORT}{YANDEX_PATH}"
    # Возврат из Яндекса — на адрес интерфейса: cookie входа ставится там же, где браузер.
    os.environ["PUBLIC_BASE_URL"] = "http://localhost:5175"
    _recreate_database()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    write_demo_env(STATE)
    _load_env()
    os.environ["YANDEX_CLIENT_ID"] = "stand-app"
    os.environ["YANDEX_CLIENT_SECRET"] = YANDEX_SECRET
    os.environ["YANDEX_DIRECT_CLIENT_ID"] = "stand-app"
    os.environ["YANDEX_DIRECT_CLIENT_SECRET"] = YANDEX_SECRET
    asyncio.run(_seed())

    from adhelper.api.app import create_app

    app = create_app()
    app.state.command_model = None
    app.state.langchain_agent = None
    app.state.site_rules = dataclasses.replace(
        load_site_rules(),
        max_guest_reads_per_address=1000,
        max_guest_reads_per_day=1000,
    )
    demo = _platform()
    demo.advertisers[DEMO_LOGIN] = Advertiser(
        kind="legal",
        inn="7707083893",
        name="ООО «Лак»",
        phone="+79991234567",
        okved="96.02",
    )
    # У каждого кабинета своя площадка: кампании демо-кабинета не считаются в бюджет нового.
    platforms: dict[str, FakePlatform] = {}

    def platform_for(_business: object, account: str) -> FakePlatform:
        if not account.startswith(STAND_LOGIN):
            return demo
        if account not in platforms:
            fake_plat = FakePlatform([], datetime.now(UTC))
            fake_plat.advertisers[account] = Advertiser(
                kind="legal",
                inn="7707083893",
                name="ООО «Ноготок»",
                phone="+79991234567",
                okved="96.02",
            )
            platforms[account] = fake_plat
        return platforms[account]

    app.state.platform_for = platform_for
    app.include_router(_stand_router(app))
    app.include_router(_yandex_router())
    # SPA catch-all маршрут должен проверяться последним, иначе он перехватывает /stand-yandex/...
    spa_route = next((r for r in app.routes if getattr(r, "name", "") == "serve_spa"), None)
    if spa_route:
        app.routes.remove(spa_route)
        app.routes.append(spa_route)
    asyncio.run(_serve(app))


if __name__ == "__main__":
    main()
