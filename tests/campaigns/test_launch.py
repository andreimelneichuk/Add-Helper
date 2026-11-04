"""Запуск на настоящем журнале: PostgreSQL, поддельная площадка и сбои посреди создания."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.preflight import StoredPeriodPreflight
from adhelper.budget.domain.limits import CampaignBudget
from adhelper.campaigns.adapters.launch_sql import SqlLaunches
from adhelper.campaigns.adapters.rules_file import load_launch_rules
from adhelper.campaigns.application.launch import (
    LaunchInputs,
    LaunchStatus,
    launch,
    launch_state,
)
from adhelper.campaigns.domain.launch import LaunchProblem, NewAd
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.rollback import RollbackStatus, rollback_change
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeStatus
from adhelper.platforms.adapters.gateway import RoutingGateway
from adhelper.platforms.domain.ports import Advertiser
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RULES = load_launch_rules("launch-1")
BUDGET = load_rules("budget-1")
MICROS = 1_000_000
Sessions = async_sessionmaker[AsyncSession]
INPUTS = LaunchInputs(
    title="Ноготок",
    account="acc",
    monthly=20_000 * MICROS,
    ads=(NewAd("Маникюр на Тверской", "Маникюр за 1700 ₽: Москва, ул. Тверская, 7."),),
    href="https://nogti.ru/",
    address="Москва, ул. Тверская, 7",
    counters=(98765432,),
)


@pytest.fixture
async def business(sessions: Sessions) -> uuid.UUID:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id, "acc")
    return business_id


def _ledger(
    platform: FakePlatform, sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> Ledger:
    return Ledger(
        repository=SqlChangeRepository(sessions, business),
        gateway=RoutingGateway(PlatformChangeGateway(platform), platform, platform, platform),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=StoredPeriodPreflight(
            SqlPeriodStore(sessions), platform, business, lambda: platform.now, load_rules
        ),
    )


async def _launch(
    platform: FakePlatform,
    sessions: Sessions,
    engine: AsyncEngine,
    business: uuid.UUID,
    inputs: LaunchInputs = INPUTS,
) -> object:
    return await launch(
        SqlLaunches(sessions, business),
        _ledger(platform, sessions, engine, business),
        inputs,
        RULES,
        BUDGET,
        NOW,
    )


async def _changes(sessions: Sessions, business: uuid.UUID) -> list[dict[str, object]]:
    async with sessions() as session:
        rows = await session.execute(
            text("SELECT kind, status, before, after FROM changes WHERE business_id = :b"),
            {"b": business},
        )
        return [dict(row._mapping) for row in rows]


async def test_launch_creates_campaign_through_journal(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    result = await _launch(platform, sessions, engine, business)
    assert result.status is LaunchStatus.DONE  # type: ignore[attr-defined]
    [created] = result.created  # type: ignore[attr-defined]
    assert created.limit == 18_000 * MICROS
    assert created.ad_ids and platform.ads[f"ad:{created.ad_ids[0]}"] is True

    # Лимит стоит на площадке, сумма лимитов в рамках рабочего бюджета в любой момент.
    assert max(platform.limit_sums) <= 18_000 * MICROS
    [change] = await _changes(sessions, business)
    assert change["kind"] == ChangeKind.CREATE_CAMPAIGN.value
    assert change["status"] == ChangeStatus.APPLIED.value
    assert change["before"] == {}
    assert change["after"]["campaign_id"] == created.campaign_id  # type: ignore[index]

    # Период бюджета начался вместе с запуском: его видит страж.
    period = await SqlPeriodStore(sessions).active_for(business, "acc", NOW)
    assert period is not None and period.period.hard_limit == 20_000 * MICROS
    state = await launch_state(
        SqlLaunches(sessions, business), _ledger(platform, sessions, engine, business)
    )
    assert state is not None and state.status is LaunchStatus.DONE


async def test_lost_response_does_not_create_second_campaign(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    platform.faults.append(Fault("create_campaign", after_apply=True))
    first = await _launch(platform, sessions, engine, business)
    assert first.status is LaunchStatus.PENDING  # type: ignore[attr-defined]

    again = await _launch(platform, sessions, engine, business)
    assert again.status is LaunchStatus.DONE  # type: ignore[attr-defined]
    # Повтор начался с чтения: кампания нашлась по метке, второй раз не создавали.
    assert platform.create_calls == 1
    assert [c["status"] for c in await _changes(sessions, business)] == ["applied"]


async def test_platform_down_keeps_launch_pending_until_retry(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    platform.unavailable = True
    assert (await _launch(platform, sessions, engine, business)).status is (  # type: ignore[attr-defined]
        LaunchStatus.PENDING
    )
    assert platform.create_calls == 0
    platform.unavailable = False
    assert (await _launch(platform, sessions, engine, business)).status is (  # type: ignore[attr-defined]
        LaunchStatus.DONE
    )
    assert platform.create_calls == 1


async def test_outside_campaigns_count_against_budget(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    # В кабинете уже есть кампания с большим лимитом: создание вышло бы за рабочий бюджет.
    platform = FakePlatform([CampaignBudget("77", 10_000 * MICROS, 0, False, None)], NOW)
    result = await _launch(platform, sessions, engine, business)
    assert result.status is LaunchStatus.REFUSED  # type: ignore[attr-defined]
    assert result.reason == "exceeds_operating_budget"  # type: ignore[attr-defined]
    assert platform.create_calls == 0


async def test_problem_stores_nothing(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    poor = LaunchInputs(**{**INPUTS.__dict__, "monthly": 1_550 * MICROS})
    result = await _launch(platform, sessions, engine, business, poor)
    assert result.status is LaunchStatus.PROBLEM  # type: ignore[attr-defined]
    assert result.problem is LaunchProblem.BELOW_PLATFORM_MIN  # type: ignore[attr-defined]
    assert result.minimum_monthly == 1_667 * MICROS  # type: ignore[attr-defined]
    assert await SqlPeriodStore(sessions).active_for(business, "acc", NOW) is None
    assert (
        await launch_state(
            SqlLaunches(sessions, business), _ledger(platform, sessions, engine, business)
        )
        is None
    )


async def test_repeat_after_done_changes_nothing(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    await _launch(platform, sessions, engine, business)
    again = await _launch(platform, sessions, engine, business)
    assert again.status is LaunchStatus.DONE  # type: ignore[attr-defined]
    assert platform.create_calls == 1
    assert len(await _changes(sessions, business)) == 1


async def test_created_campaign_is_not_rolled_back(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    await _launch(platform, sessions, engine, business)
    async with sessions() as session:
        change_id = await session.scalar(
            text("SELECT id FROM changes WHERE business_id = :b"), {"b": business}
        )
    # Объекты не удаляем: созданную кампанию останавливают паузой.
    result = await rollback_change(_ledger(platform, sessions, engine, business), int(change_id))
    assert result.status is RollbackStatus.NOT_REVERSIBLE


async def test_running_period_blocks_launch(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    from adhelper.budget.domain.limits import BudgetPeriod

    await SqlPeriodStore(sessions).add(
        business,
        "acc",
        BudgetPeriod(30_000 * MICROS, 1000, NOW.replace(day=1), NOW.replace(month=12)),
        "budget-1",
        None,
        NOW,
    )
    platform = FakePlatform([], NOW)
    result = await _launch(platform, sessions, engine, business)
    assert result.status is LaunchStatus.REFUSED  # type: ignore[attr-defined]
    assert result.reason == "period_exists"  # type: ignore[attr-defined]
    assert platform.create_calls == 0


ADVERTISER = Advertiser("legal", "7707083893", "ООО «Ноготок»", None, "96.02")


def _marked_ledger(
    platform: FakePlatform,
    sessions: Sessions,
    engine: AsyncEngine,
    business: uuid.UUID,
    source: Advertiser | None = ADVERTISER,
) -> Ledger:
    async def advertiser() -> Advertiser | None:
        return source

    return Ledger(
        repository=SqlChangeRepository(sessions, business),
        gateway=RoutingGateway(
            PlatformChangeGateway(platform),
            platform,
            platform,
            platform,
            registry=platform,
            advertiser=advertiser,
        ),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=StoredPeriodPreflight(
            SqlPeriodStore(sessions), platform, business, lambda: platform.now, load_rules
        ),
    )


async def _marked_launch(
    platform: FakePlatform, sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> object:
    return await launch(
        SqlLaunches(sessions, business),
        _marked_ledger(platform, sessions, engine, business),
        INPUTS,
        RULES,
        BUDGET,
        NOW,
        advertiser=ADVERTISER.card(),
    )


async def test_marking_goes_to_platform_before_campaigns_and_inn_stays_out_of_journal(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    result = await _marked_launch(platform, sessions, engine, business)
    assert result.status is LaunchStatus.DONE  # type: ignore[attr-defined]
    assert platform.advertisers["acc"] == ADVERTISER
    async with sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT kind, status, planned::text, before::text, after::text FROM changes"
                    " WHERE business_id = :b ORDER BY id"
                ),
                {"b": business},
            )
        ).all()
    assert [row[0] for row in rows] == ["set_advertiser", "create_campaign"]
    assert rows[0][1] == ChangeStatus.APPLIED.value
    # Реквизиты не в журнале: только тип, конец ИНН и ОКВЭД.
    assert all("7707083893" not in "".join(row[2:]) for row in rows)
    assert "3893" in rows[0][2]


async def test_lost_marking_reply_is_finished_by_reading_not_sent_twice(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    platform.faults.append(Fault("set_advertiser", after_apply=True))
    first = await _marked_launch(platform, sessions, engine, business)
    assert first.status is LaunchStatus.PENDING  # type: ignore[attr-defined]
    assert platform.created == {}
    again = await _marked_launch(platform, sessions, engine, business)
    assert again.status is LaunchStatus.DONE  # type: ignore[attr-defined]
    assert platform.set_advertiser_calls == 1


async def test_marking_without_key_waits_and_campaign_waits_behind_it(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    # Процесс без ключа шифрования реквизитов (воркер) отправить их не может: запись ждёт.
    platform = FakePlatform([], NOW)
    ledger = _marked_ledger(platform, sessions, engine, business, source=None)
    result = await launch(
        SqlLaunches(sessions, business),
        ledger,
        INPUTS,
        RULES,
        BUDGET,
        NOW,
        advertiser=ADVERTISER.card(),
    )
    assert result.status is not LaunchStatus.DONE
    assert platform.advertisers == {} and platform.created == {}


async def test_marking_already_on_platform_is_not_sent_again(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    platform = FakePlatform([], NOW)
    platform.advertisers["acc"] = ADVERTISER
    result = await _marked_launch(platform, sessions, engine, business)
    assert result.status is LaunchStatus.DONE  # type: ignore[attr-defined]
    assert platform.set_advertiser_calls == 0


async def test_marking_changed_after_recording_is_not_sent_and_campaigns_wait(
    sessions: Sessions, engine: AsyncEngine, business: uuid.UUID
) -> None:
    # Запись журнала говорит об одном ИНН, а в хранилище уже другой: отправлять нельзя ни тот,
    # ни другой, пока запуск не запишет новые данные новой записью.
    platform = FakePlatform([], NOW)
    other = Advertiser("legal", "7736050003", "ООО «Ноготок»", None, "96.02")
    ledger = _marked_ledger(platform, sessions, engine, business, source=other)
    result = await launch(
        SqlLaunches(sessions, business),
        ledger,
        INPUTS,
        RULES,
        BUDGET,
        NOW,
        advertiser=ADVERTISER.card(),
    )
    assert result.status is LaunchStatus.PENDING
    assert platform.advertisers == {} and platform.created == {}
