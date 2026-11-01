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
from adhelper.creatives.adapters.rules_file import load_rules, load_slot_rules
from adhelper.creatives.adapters.sql import SqlAdSlots, SqlProfileSource
from adhelper.creatives.application.service import CURRENT_RULES, CURRENT_SLOT_RULES
from adhelper.creatives.domain.lifecycle import plan_slots
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
        creatives = (await client.get(base + "/creatives", headers=headers)).json()
        month = {"since": "2026-10-22"}
        campaign = (
            await client.get(base + "/campaigns/lak-studio-2/101", headers=headers, params=month)
        ).json()
    await app.state.engine.dispose()

    money = overview["money"][0]
    assert money["balance_gap"] == 1_240_000_000
    assert money["period"]["spent"] > 0
    assert money["balance_gap"] > money["balance_tolerance"]
    assert [a["kind"] for a in overview["alerts"]] == ["balance_mismatch"]
    assert {c["state"] for c in overview["campaigns"]} == {"suspended"}
    assert len(overview["days"]) == 14
    assert {line["source"] for line in history} >= {"autopilot", "budget_guard", "user"}
    assert {c["state"] for c in creatives["creatives"]} >= {"needs_owner", "launch_pending"}
    assert [w["campaign_name"] for w in creatives["writing"]] == ["Маникюр — сайты и приложения"]
    # Объявления делят день направления без остатка: суммы сходятся с отчётом кампании.
    for key in ("impressions", "clicks", "cost", "conversions"):
        assert (
            sum(ad["totals"][key] for ad in campaign["ads"]) == campaign["campaign"]["totals"][key]
        )
    assert [ad["state"] for ad in campaign["ads"]] == ["on", "on", "suspended"]


async def test_demo_ads_are_already_where_the_keeper_would_put_them(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    demo = await seed_demo(sessions, NOW, tmp_path)
    slots = SqlAdSlots(sessions, demo.business_id)
    profile = await SqlProfileSource(sessions).profile(demo.business_id)
    assert profile is not None
    plan = plan_slots(
        profile,
        await slots.campaigns(),
        await slots.candidates(),
        load_slot_rules(CURRENT_SLOT_RULES),
        await slots.open_sets(),
        load_rules(CURRENT_RULES).max_sets_per_day,
        False,
    )
    # Иначе первый же цикл хранителя переставит демо и закажет тексты у модели.
    assert plan.moves == () and plan.requests == ()
    assert plan.attention == ("ads_need_confirmation",)
