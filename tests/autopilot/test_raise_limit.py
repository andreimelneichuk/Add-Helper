"""Рост лимита эффективной кампании в режиме «Сам»: свободные деньги или перенос.

Есть запас до рабочего бюджета — лимит растёт через стража бюджета и журнал. Запаса нет —
деньги переносятся с кампании без эффективных объявлений: сначала снижение, потом рост.
Не вышло — карточка владельцу, площадка не тронута. docs/04-autopilot.md §4.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.rules_file import load_policy_rules
from adhelper.autopilot.adapters.transfers import SqlTransferStore
from adhelper.autopilot.application.raise_limit import raise_limit
from adhelper.autopilot.application.transfer import CampaignTraits, PolicyFacts
from adhelper.autopilot.domain.policy import PolicyAction
from adhelper.budget.adapters.ledgered import LedgeredBudgetPlatform, PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.application.preflight import BudgetPreflight
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget
from adhelper.budget.domain.watch import BurnRate
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeSource
from tests.budget.fake_platform import FakePlatform
from tests.seed import seed_account

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
BUDGET_RULES = replace(
    load_rules("budget-1"),
    max_increase_bp=1_000_000,
    min_increase_interval_days=0,
    change_spike_old_bp=0,
    change_spike_new_bp=0,
)
POLICY = load_policy_rules("policy-1")
# Рабочий бюджет 900 000: 10% резерва от 1 000 000.
PERIOD = BudgetPeriod(hard_limit=1_000_000, reserve_bp=1000)
ZERO = BurnRate.zero()
BUSINESS = uuid.uuid4()
MANUAL = {"a": CampaignTraits(False, False), "b": CampaignTraits(False, False)}
FACTS = PolicyFacts(
    allowed=frozenset({PolicyAction.INCREASE_LIMIT}),
    changes_today=0,
    moved_this_week=0,
    data_alert=False,
    api_points_rest=10_000,
)
Sessions = async_sessionmaker[AsyncSession]


def campaign(campaign_id: str, limit: int, spent: int = 0) -> CampaignBudget:
    return CampaignBudget(campaign_id, limit, spent, True, None)


@pytest.fixture
async def _owner(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


def ledgered(platform: FakePlatform, sessions: Sessions, engine: AsyncEngine) -> Any:
    ledger = Ledger(
        repository=SqlChangeRepository(sessions, BUSINESS),
        gateway=PlatformChangeGateway(platform),
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=BudgetPreflight(platform, PERIOD, ZERO, lambda: platform.now, BUDGET_RULES),
    )
    return LedgeredBudgetPlatform(platform, ledger, ChangeSource.AUTOPILOT)


async def run(
    platform: FakePlatform,
    sessions: Sessions,
    engine: AsyncEngine,
    new_limit: int,
    donors: tuple[str, ...] = ("a",),
    traits: dict[str, CampaignTraits] = MANUAL,
) -> str:
    return await raise_limit(
        ledgered(platform, sessions, engine),
        SqlTransferStore(sessions, BUSINESS),
        "acc",
        PERIOD,
        "b",
        new_limit,
        donors,
        FACTS,
        traits,
        ZERO,
        NOW,
        BUDGET_RULES,
        POLICY,
    )


async def test_free_budget_raises_the_limit_through_the_journal(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 300_000), campaign("b", 100_000)], NOW)

    status = await run(platform, sessions, engine, 120_000)

    assert status == "executed"
    assert [(c.campaign_id, c.new_limit) for c in platform.set_limit_calls] == [("b", 120_000)]
    assert platform.campaigns["a"].limit == 300_000
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert [(r.source, r.target_ref) for r in rows] == [(ChangeSource.AUTOPILOT, "b")]


async def test_without_free_budget_money_moves_from_the_donor_first(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 400_000)], NOW)

    status = await run(platform, sessions, engine, 450_000)

    assert status == "executed"
    assert [c.campaign_id for c in platform.set_limit_calls] == ["a", "b"]
    assert platform.campaigns["a"].limit == 450_000
    assert platform.campaigns["b"].limit == 450_000
    assert max(platform.limit_sums) <= PERIOD.operating_budget
    records = await SqlTransferStore(sessions, BUSINESS).open_transfers()
    assert records == []


async def test_without_free_budget_and_donor_the_owner_gets_a_card(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 400_000)], NOW)

    assert await run(platform, sessions, engine, 450_000, donors=()) == "awaiting_confirmation"
    assert platform.set_limit_calls == []


async def test_donor_that_already_spent_its_limit_is_not_used(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000, spent=480_000), campaign("b", 400_000)], NOW)

    assert await run(platform, sessions, engine, 450_000) == "awaiting_confirmation"
    assert platform.set_limit_calls == []


async def test_policy_refusal_of_the_transfer_gives_a_card(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 500_000), campaign("b", 400_000)], NOW)

    # Про стратегию ничего не знаем: политика отказывает, деньги не двигаются.
    assert await run(platform, sessions, engine, 450_000, traits={}) == "awaiting_confirmation"
    assert platform.set_limit_calls == []


async def test_platform_down_changes_nothing(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 300_000), campaign("b", 100_000)], NOW)
    platform.unavailable = True

    assert await run(platform, sessions, engine, 120_000) == "awaiting_confirmation"
    platform.unavailable = False
    assert platform.set_limit_calls == []


async def test_limit_already_at_target_is_done(
    sessions: Sessions, engine: AsyncEngine, _owner: None
) -> None:
    platform = FakePlatform([campaign("a", 300_000), campaign("b", 120_000)], NOW)

    assert await run(platform, sessions, engine, 120_000) == "executed"
    assert platform.set_limit_calls == []
