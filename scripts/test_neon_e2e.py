import asyncio
import sys
from dataclasses import replace

import httpx
from dotenv import load_dotenv
from sqlalchemy import text

from adhelper.api.app import create_app
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.config import Settings
from adhelper.db import create_engine, ping
from adhelper.worker.app import connector_for

load_dotenv()


def _say(msg: str = "") -> None:
    sys.stdout.write(f"{msg}\n")


async def verify_all() -> None:
    _say("==================================================")
    _say("NEON LAKEBASE POSTGRES END-TO-END VERIFICATION")
    _say("==================================================")

    settings = Settings()
    _say(f"Branch: {settings.neon_branch}")
    _say(f"Pooled Host: {settings.database_url.host}")
    _say(f"Unpooled Host: {settings.migration_database_url.host}")

    # 1. Test Pooled SQLAlchemy Async Engine
    engine = create_engine(settings)
    await ping(engine)
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT current_database(), current_user, version()"))
        row = res.fetchone()
        assert row is not None
        _say("\n[1] Pooled Async Engine (Web/API):")
        _say(f"    Database: {row[0]}")
        _say(f"    User: {row[1]}")
        _say(f"    Postgres Version: {row[2].split()[0]} {row[2].split()[1]}")
    await engine.dispose()

    # 2. Test Unpooled Worker Connector (Procrastinate LISTEN/NOTIFY)
    connector = connector_for(settings)
    await connector.open_async()
    res_worker = await connector.execute_query_one_async("SELECT current_database(), current_user")
    _say("\n[2] Unpooled Direct Connector (Worker/Alembic):")
    _say(f"    Database: {res_worker['current_database']}")
    _say(f"    User: {res_worker['current_user']}")
    await connector.close_async()

    # 3. Test Full FastAPI App End-to-End
    app = create_app()
    app.state.site_rules = replace(load_site_rules(), max_guest_reads_per_address=100)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        # Healthcheck
        r_health = await client.get("/healthz")
        _say("\n[3] Full Application End-to-End:")
        _say(f"    GET /healthz: {r_health.status_code} {r_health.json()}")
        assert r_health.status_code == 200

        # SPA Serving
        r_spa = await client.get("/")
        _say(f"    GET / (SPA frontend): {r_spa.status_code} ({len(r_spa.text)} bytes)")
        assert r_spa.status_code == 200

        # Database Write
        r_write = await client.post("/api/site-reads", json={"url": "https://neon-beauty-salon.ru"})
        write_data = r_write.json()
        _say(
            f"    POST /api/site-reads (Write to Neon): {r_write.status_code} "
            f"(id: {write_data['id']}, token: {write_data['token'][:10]}...)"
        )
        assert r_write.status_code == 202
        read_id = write_data["id"]
        token = write_data["token"]

        # Database Read
        r_read = await client.get(f"/api/site-reads/{read_id}", headers={"X-Read-Token": token})
        read_data = r_read.json()
        _say(
            f"    GET /api/site-reads/{read_id} (Read from Neon): {r_read.status_code} "
            f"(url: {read_data['url']}, status: {read_data['status']})"
        )
        assert r_read.status_code == 200
        assert read_data["url"] == "https://neon-beauty-salon.ru/"

    await app.state.engine.dispose()
    _say("\n>>> ALL NEON SERVICES VERIFIED AND FUNCTIONAL! <<<")


if __name__ == "__main__":
    asyncio.run(verify_all())


if __name__ == "__main__":
    asyncio.run(verify_all())
