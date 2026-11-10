"""Данные цикла из витрины и подготовка теста на настоящем PostgreSQL."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.data import SqlAutopilotData
from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.autopilot.adapters.tests import SqlTestPlanner
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.domain.model import BusinessProfile, Service
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
OTHER = uuid.uuid4()
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 20)
RULES = load_rules("autopilot-2")
Sessions = async_sessionmaker[AsyncSession]


async def run(sessions: Sessions, sql: str, **values: object) -> None:
    async with sessions.begin() as session:
        await session.execute(text(sql), values)


async def campaign(sessions: Sessions, business: uuid.UUID, cid: str, state: str = "on") -> None:
    await run(
        sessions,
        "INSERT INTO campaigns (business_id, account, campaign_id, name, state, limit_micros,"
        " targeting) VALUES (:b, 'acc', :c, 'Маникюр', :s, 800000000, '{}'::jsonb)",
        b=business,
        c=cid,
        s=state,
    )


async def ad(
    sessions: Sessions, business: uuid.UUID, ad_id: str, cid: str, state: str = "on"
) -> None:
    await run(
        sessions,
        "INSERT INTO ads (business_id, account, ad_id, campaign_id, title, text, href, state)"
        " VALUES (:b, 'acc', :a, :c, 'Маникюр', 'Текст', 'https://x.example', :s)",
        b=business,
        a=ad_id,
        c=cid,
        s=state,
    )


async def stats(sessions: Sessions, business: uuid.UUID, ad_id: str, days: int) -> None:
    for back in range(days):
        await run(
            sessions,
            "INSERT INTO ad_stats (business_id, account, ad_id, day, impressions, clicks,"
            " cost_micros, conversions) VALUES (:b, 'acc', :a, :d, 1000, 20, 100000000, 1)",
            b=business,
            a=ad_id,
            d=TODAY - timedelta(days=back),
        )


@pytest.fixture(autouse=True)
async def _seed(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    await seed_account(sessions, OTHER, "acc")
    await campaign(sessions, BUSINESS, "c1")
    await ad(sessions, BUSINESS, "1", "c1")
    await stats(sessions, BUSINESS, "1", 30)
    # Чужой бизнес с тем же логином и номерами не должен попасть в данные.
    await campaign(sessions, OTHER, "c1")
    await ad(sessions, OTHER, "1", "c1")
    await stats(sessions, OTHER, "1", 30)


async def test_totals_use_only_mature_days_of_own_business(sessions: Sessions) -> None:
    data = await SqlAutopilotData(sessions, BUSINESS, RULES).load("acc", NOW)

    [only] = data.ads
    # 30 дней, из них последние 2 ещё дозревают.
    assert (only.spend, only.leads) == (28 * 100_000_000, 28)
    assert only.ref == "ad:1" and only.campaign_id == "c1" and only.active
    assert only.age_days == 29
    assert data.campaigns[0].limit == 800_000_000
    assert data.recent.clicks == 7 * 20
    assert data.recent.visits is None


async def test_ad_without_known_group_is_its_own_group(sessions: Sessions) -> None:
    await ad(sessions, BUSINESS, "2", "c1")
    data = await SqlAutopilotData(sessions, BUSINESS, RULES).load("acc", NOW)
    assert {a.group_id for a in data.ads} == {"ad:1", "ad:2"}


async def test_ads_of_one_platform_group_share_it(sessions: Sessions) -> None:
    # Группа с площадки: два объявления одной группы — пауза одного из них допустима.
    await ad(sessions, BUSINESS, "2", "c1")
    await run(sessions, "UPDATE ads SET group_id = '777' WHERE business_id = :b", b=BUSINESS)
    data = await SqlAutopilotData(sessions, BUSINESS, RULES).load("acc", NOW)
    assert {a.group_id for a in data.ads} == {"group:777"}


async def test_suspended_campaign_makes_its_ads_inactive(sessions: Sessions) -> None:
    await campaign(sessions, BUSINESS, "c2", "suspended")
    await ad(sessions, BUSINESS, "3", "c2")
    data = await SqlAutopilotData(sessions, BUSINESS, RULES).load("acc", NOW)
    assert {a.ref: a.active for a in data.ads} == {"ad:1": True, "ad:3": False}


async def test_declined_offer_is_held_for_two_weeks(sessions: Sessions) -> None:
    log = SqlDecisionLog(sessions, BUSINESS)
    await log.record("acc", "ad:1", "borderline", "start_test", "declined", {}, "x", NOW)
    data = await SqlAutopilotData(sessions, BUSINESS, RULES).load("acc", NOW)
    assert data.ads[0].test_hold

    later = NOW + timedelta(days=RULES.test_decline_hold_days)
    data = await SqlAutopilotData(sessions, BUSINESS, RULES).load("acc", later)
    assert not data.ads[0].test_hold


async def test_prepared_test_marks_campaign_as_testing(sessions: Sessions) -> None:
    planner = SqlTestPlanner(sessions, BUSINESS, "experiments-1")
    assert await planner.prepare("acc", "c1", "ad:1", "price", 1) == "prepared"
    assert await planner.prepare("acc", "c1", "ad:1", "benefit", 2) == "test_running"

    data = await SqlAutopilotData(sessions, BUSINESS, RULES).load("acc", NOW)
    assert data.campaigns[0].test_running
    other = await SqlAutopilotData(sessions, OTHER, RULES).load("acc", NOW)
    assert not other.campaigns[0].test_running


async def test_hypothesis_comes_from_profile_and_skips_tried(sessions: Sessions) -> None:
    planner = SqlTestPlanner(sessions, BUSINESS, "experiments-1")
    assert await planner.hypothesis("acc", "ad:1") is None

    profile = BusinessProfile("Лак", (Service("Маникюр", 1500),), features=("у метро",))
    await SqlProfiles(sessions, BUSINESS).save(profile, "")
    assert await planner.hypothesis("acc", "ad:1") == "price"

    await planner.prepare("acc", "c1", "ad:1", "price", 1)
    assert await planner.hypothesis("acc", "ad:1") == "benefit"
