"""Демо-кабинет: только на локальной машине, сессия только в файле, экраны получают данные."""

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.api.app import create_app
from adhelper.config import Settings
from adhelper.worker.demo import (
    ENV_FILE,
    SESSION_FILE,
    NotLocal,
    require_local,
    seed_demo,
    write_demo_env,
)

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("url", ["https://adhelper.example", "http://10.0.0.5:8000"])
def test_refuses_outside_localhost(url: str) -> None:
    with pytest.raises(NotLocal):
        require_local(url)


async def test_demo_cabinet_is_readable_through_api(
    migrated: Settings, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    write_demo_env(tmp_path)
    first = await seed_demo(sessions, NOW, tmp_path, mismatch=True)
    again = await seed_demo(sessions, NOW, tmp_path, mismatch=True)
    assert first.created and not again.created
    assert again.business_id == first.business_id
    for name in (SESSION_FILE, ENV_FILE):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600

    demo = json.loads((tmp_path / SESSION_FILE).read_text())
    env = dict(
        line.split("=", 1) for line in (tmp_path / ENV_FILE).read_text().splitlines() if line
    )
    settings = migrated.model_copy(
        update={"token_encryption_keys": SecretStr(env["TOKEN_ENCRYPTION_KEYS"])}
    )
    app = create_app(settings)
    app.state.clock = lambda: NOW
    headers = {"Cookie": demo["cookie"]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        base = f"/api/businesses/{demo['business_id']}"
        overview = (await client.get(base + "/overview", headers=headers)).json()
        history = (await client.get(base + "/history", headers=headers)).json()
        sets = (await client.get(base + "/creative-sets", headers=headers)).json()
    await app.state.engine.dispose()

    money = overview["money"][0]
    assert money["balance_gap"] == 1_240_000_000
    assert money["period"]["spent"] > 0
    assert money["balance_gap"] > money["balance_tolerance"]
    assert [a["kind"] for a in overview["alerts"]] == ["balance_mismatch"]
    assert {c["state"] for c in overview["campaigns"]} == {"suspended"}
    assert len(overview["days"]) == 14
    assert {line["source"] for line in history} >= {"autopilot", "budget_guard", "user"}
    assert sets[0]["status"] == "ready"
