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


async def test_responses_carry_security_headers_and_hide_docs(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/healthz")
            docs = await client.get("/docs")
            schema = await client.get("/openapi.json")
    finally:
        await app.state.engine.dispose()

    assert health.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in health.headers["content-security-policy"]
    assert "strict-transport-security" in health.headers
    assert docs.status_code != 200 or "swagger" not in docs.text.lower()
    assert schema.status_code != 200 or "openapi" not in schema.text[:50].lower()


async def test_big_body_is_refused_before_the_handler(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            big = await client.post("/api/site-reads", content=b"a" * 1_100_000)
            small = await client.post("/api/site-reads", json={"url": ""})
    finally:
        await app.state.engine.dispose()
    assert big.status_code == 413
    assert small.status_code != 413


async def test_spa_prevents_path_traversal(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Нормальный роут SPA отдаёт HTML
            start = await client.get("/start")
            assert start.status_code == 200
            assert "text/html" in start.headers.get("content-type", "")

            # Попытки выхода за границы frontend/dist не должны отдавать целевой файл
            traversal = await client.get("/../../pyproject.toml")
            assert "[project]" not in traversal.text

            encoded = await client.get("/%2e%2e/%2e%2e/pyproject.toml")
            assert "[project]" not in encoded.text
    finally:
        await app.state.engine.dispose()


def test_guest_key_prefers_platform_headers() -> None:
    from starlette.requests import Request

    from adhelper.api.site import _guest_key

    class DummyApp:
        class State:
            guest_secret = b"test-secret-key"

        state = State()

    def make_req(client_ip: str | None, headers: dict[str, str] | None = None) -> Request:
        scope = {
            "type": "http",
            "app": DummyApp(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": (client_ip, 12345) if client_ip else None,
        }
        return Request(scope)

    k1 = _guest_key(make_req("198.51.100.5"))
    # Поддельный X-Forwarded-For не меняет ключ
    k_spoof = _guest_key(make_req("198.51.100.5", {"x-forwarded-for": "1.2.3.4"}))
    assert k1 == k_spoof

    # Платформенные заголовки Cloudflare и Render имеют приоритет
    k_cf = _guest_key(make_req("10.0.0.1", {"cf-connecting-ip": "198.51.100.5"}))
    assert k_cf == k1

    k_render = _guest_key(make_req("10.0.0.1", {"render-client-ip": "198.51.100.5"}))
    assert k_render == k1
