"""Тест по итогам анализа доходит до показов: вариант через журнал в группе контроля,
проверка Яндекса, одновременный показ, отчёт по объявлениям и итог на подставной площадке.
Сценарий 30 из приёмочных.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.domain.limits import CampaignBudget
from adhelper.creatives.adapters.rules_file import load_rules as load_creative_rules
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.application.service import CURRENT_RULES
from adhelper.creatives.domain.model import BusinessProfile, Service
from adhelper.experiments.adapters.ledgered import LedgeredSwitch
from adhelper.experiments.adapters.pipeline_sql import (
    SqlAdStats,
    SqlObservations,
    SqlVariantContext,
)
from adhelper.experiments.adapters.rules_file import load_rules
from adhelper.experiments.adapters.sql import SqlExperimentStore
from adhelper.experiments.application.pipeline import run_experiments, sync_test_stats
from adhelper.experiments.application.service import ExperimentService
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource
from adhelper.platforms.adapters.gateway import RoutingGateway
from adhelper.platforms.domain.ports import AdDay
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RULES = load_rules("experiments-2")
CREATIVE_RULES = load_creative_rules(CURRENT_RULES)
PROFILE = BusinessProfile(
    "Лак", (Service("Маникюр", 1500),), "ул. Ленина, 5", ("Стерильные инструменты",), "маникюр"
)
Sessions = async_sessionmaker[AsyncSession]


class Stand:
    def __init__(self, sessions: Sessions, engine: AsyncEngine, business: uuid.UUID) -> None:
        self.sessions = sessions
        self.business = business
        self.now = START
        self.platform = FakePlatform([CampaignBudget("c1", 5_000_000_000, 0, True, None)], START)
        self.platform.ads = {"ad:500": True}
        self.platform.groups["500"] = "g-500"
        self.ledger = Ledger(
            SqlChangeRepository(sessions, business),
            RoutingGateway(
                PlatformChangeGateway(self.platform), self.platform, publisher=self.platform
            ),
            PostgresAccountLock(engine),
            3,
        )
        self.store = SqlExperimentStore(sessions, business)

    async def run(self) -> None:
        service = ExperimentService(
            self.store, LedgeredSwitch(self.ledger), self.platform, lambda: self.now, RULES
        )
        await run_experiments(
            "lak",
            self.store,
            service,
            self.ledger,
            self.platform,
            SqlVariantContext(self.sessions, self.business),
            SqlObservations(self.sessions, self.business),
            CREATIVE_RULES,
        )

    async def sync(self) -> int:
        return await sync_test_stats(
            "lak", self.store, self.platform, SqlAdStats(self.sessions, self.business), self.now
        )

    async def stats(self, ad_id: str, day: date, clicks: int, leads: int) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO ad_stats (business_id, account, ad_id, day, impressions,"
                    " clicks, cost_micros, conversions) VALUES (:b, 'lak', :i, :d, 0, :c,"
                    " :m, :l)"
                ),
                {
                    "b": self.business,
                    "i": ad_id,
                    "d": day,
                    "c": clicks,
                    "m": clicks * 10**6,
                    "l": leads,
                },
            )

    async def changes(self) -> list[Any]:
        return list(await SqlChangeRepository(self.sessions, self.business).all())


@pytest.fixture
async def stand(sessions: Sessions, engine: AsyncEngine) -> Stand:
    business = uuid.uuid4()
    await seed_account(sessions, business, "lak")
    await SqlProfiles(sessions, business).save(PROFILE, "https://lak.example/")
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO campaigns (business_id, account, campaign_id, name, state)"
                " VALUES (:b, 'lak', 'c1', 'Маникюр', 'on')"
            ),
            {"b": business},
        )
        await session.execute(
            text(
                "INSERT INTO ads (business_id, account, ad_id, campaign_id, title, text, href,"
                " state) VALUES (:b, 'lak', '500', 'c1', 'Маникюр в «Лак»',"
                " 'Ждём вас: ул. Ленина, 5.', 'https://lak.example/', 'on')"
            ),
            {"b": business},
        )
    result = Stand(sessions, engine, business)
    await result.store.add("lak", "c1", "ad:500", None, "price", RULES.version)
    return result


async def test_prepared_test_gets_price_variant_through_ledger_held_until_check(
    stand: Stand,
) -> None:
    await stand.run()
    (published,) = stand.platform.published.values()
    assert "1500 ₽" in f"{published.headline} {published.text}"
    assert published.href == "https://lak.example/"
    # Вариант встаёт в группу контроля: Яндекс делит показы внутри одной группы.
    assert published.group_id == stand.platform.groups["500"] == "g-500"
    # Вариант ждёт проверки выключенным: до старта показывается только контроль.
    assert stand.platform.ads[f"ad:{published.ad_id}"] is False
    assert stand.platform.ads["ad:500"] is True
    (record,) = await stand.store.open_for("lak")
    assert (record.status.value, record.variant_ref) == ("review", f"ad:{published.ad_id}")
    kinds = [(c.source, c.kind) for c in await stand.changes()]
    assert kinds == [
        (ChangeSource.EXPERIMENT, ChangeKind.CREATE_AD),
        (ChangeSource.EXPERIMENT, ChangeKind.PAUSE_AD),
    ]
    # Повтор ничего не создаёт второй раз.
    await stand.run()
    assert stand.platform.create_ad_calls == 1


async def test_lost_create_reply_is_finished_by_reading(stand: Stand) -> None:
    stand.platform.faults.append(Fault("create_ad", after_apply=True))
    await stand.run()
    await stand.ledger.execute_pending("lak")
    await stand.run()
    assert stand.platform.create_ad_calls == 1
    (record,) = await stand.store.open_for("lak")
    assert record.variant_ref is not None


async def test_rejected_variant_cancels_test_and_control_keeps_running(stand: Stand) -> None:
    await stand.run()
    (published,) = stand.platform.published.values()
    stand.platform.reject(published.ad_id, "Превосходная степень")
    await stand.run()
    assert await stand.store.open_for("lak") == []
    assert stand.platform.ads["ad:500"] is True


async def test_accepted_variant_runs_beside_control_and_report_decides(stand: Stand) -> None:
    await stand.run()
    (published,) = stand.platform.published.values()
    variant = published.ad_id
    stand.platform.accept(variant)
    await stand.run()
    (record,) = await stand.store.open_for("lak")
    assert record.status.value == "running"
    assert stand.platform.ads == {"ad:500": True, f"ad:{variant}": True}

    for day in range(1, 4):
        stand.now = START + timedelta(days=day)
        await stand.run()
        # Оба варианта показываются каждый день, паузы по дням не ставятся.
        assert stand.platform.ads == {"ad:500": True, f"ad:{variant}": True}

    # День старта неполный у варианта: в сравнение не идёт, иначе контроль получил бы фору.
    await stand.stats("500", START.date(), 400, 40)
    # Вариант с ценой собирает обращения заметно лучше: итог после минимального срока.
    for offset in range(1, 8):
        stat_day = (START + timedelta(days=offset)).date()
        await stand.stats("500", stat_day, 10, 0)
        await stand.stats(variant, stat_day, 10, 1)
    stand.now = START + timedelta(days=8)
    await stand.run()
    finished = await stand.store.get(record.id)
    assert finished is not None and finished.status.value == "applied"
    assert finished.winner == "B"
    assert stand.platform.ads[f"ad:{variant}"] is True
    assert stand.platform.ads["ad:500"] is False
    assert all(c.source is ChangeSource.EXPERIMENT for c in await stand.changes())


async def test_daily_report_by_ad_fills_stats_of_running_tests(stand: Stand) -> None:
    await stand.run()
    (published,) = stand.platform.published.values()
    stand.platform.accept(published.ad_id)
    await stand.run()
    first = (START + timedelta(days=1)).date()
    stand.platform.ad_report = [
        AdDay("500", first, 300, 12, 24_000_000, 1),
        AdDay(published.ad_id, first, 280, 11, 22_000_000, 2),
        AdDay("999", first, 1, 1, 1, 0),
    ]
    stand.now = START + timedelta(days=2)

    assert await stand.sync() == 2
    assert await stand.sync() == 2  # Повтор заменяет строки, а не складывает.

    async with stand.sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT ad_id, day, impressions, clicks, cost_micros, conversions"
                    " FROM ad_stats WHERE business_id = :b ORDER BY ad_id"
                ),
                {"b": stand.business},
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("500", first, 300, 12, 24_000_000, 1),
        (published.ad_id, first, 280, 11, 22_000_000, 2),
    ]
    # Отчёт спрошен по кампании теста с дня старта по вчера, по московским суткам.
    assert stand.platform.ad_report_calls == [("lak", ("c1",), START.date(), first)] * 2


async def test_without_fitting_variant_test_is_cancelled_honestly(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    business = uuid.uuid4()
    await seed_account(sessions, business, "lak")
    await SqlProfiles(sessions, business).save(BusinessProfile("Лак", (Service("Маникюр"),)), "")
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO ads (business_id, account, ad_id, campaign_id, title, text, href,"
                " state) VALUES (:b, 'lak', '500', 'c1', 'Маникюр', 'Ждём вас.',"
                " 'https://l/', 'on')"
            ),
            {"b": business},
        )
    stand = Stand(sessions, engine, business)
    test_id = await stand.store.add("lak", "c1", "ad:500", None, "price", RULES.version)
    await stand.run()
    record = await stand.store.get(test_id)
    assert record is not None
    assert (record.status.value, record.reason) == ("cancelled", "no_variant")
    assert stand.platform.create_ad_calls == 0
