"""Цикл автопилота на настоящих журнале и PostgreSQL, площадка поддельная."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.ledgered import LedgeredActions
from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.autopilot.application.cycle import AccountData, AutopilotCycle
from adhelper.autopilot.domain.decide import Mode
from adhelper.autopilot.domain.explain import explain, rubles
from adhelper.autopilot.domain.health import Window
from adhelper.autopilot.domain.model import AdStats, CampaignStats
from adhelper.budget.adapters.ledgered import LedgeredBudgetPlatform, PlatformChangeGateway
from adhelper.budget.adapters.rules_file import load_rules as load_budget_rules
from adhelper.budget.application.guard import apply_limit_change
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget, LimitChange
from adhelper.budget.domain.watch import BurnRate
from adhelper.ledger.adapters.sql import PostgresAccountLock, SqlChangeRepository
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus
from adhelper.notifications.adapters.sql import SqlAlerts
from adhelper.platforms.adapters.gateway import RoutingGateway
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TARGET = 400_000_000
RULES = load_rules("autopilot-1")
BUDGET_RULES = load_budget_rules("budget-1")
Sessions = async_sessionmaker[AsyncSession]
HEALTHY = Window(2_000_000_000, 9, 1000, 900)
BASELINE = Window(10_000_000_000, 50, 5000, 4500)


@pytest.fixture(autouse=True)
async def _owners(sessions: Sessions) -> None:
    await seed_account(sessions, BUSINESS, "acc")


BAD = AdStats("ad:bad", "c1", "g1", TARGET * 5, 0, 30, True)
GOOD = AdStats("ad:good", "c1", "g1", TARGET * 10, 20, 30, True)


@dataclass
class Source:
    data: AccountData

    async def load(self, account: str, now: datetime) -> AccountData:
        return self.data


def data(
    ads: list[AdStats],
    campaigns: list[CampaignStats] | None = None,
    recent: Window = HEALTHY,
    incident: bool = False,
) -> AccountData:
    return AccountData(ads, campaigns or [], recent, BASELINE, incident, False)


donors_seen: list[tuple[str, ...]] = []


def build(
    source: AccountData, platform: FakePlatform, sessions: Sessions, engine: AsyncEngine, hard: int
) -> AutopilotCycle:
    ledger = Ledger(
        SqlChangeRepository(sessions, BUSINESS),
        RoutingGateway(PlatformChangeGateway(platform), platform),
        PostgresAccountLock(engine),
        3,
    )
    budget_platform = LedgeredBudgetPlatform(platform, ledger, ChangeSource.AUTOPILOT)

    async def increase(
        account: str, campaign_id: str, new_limit: int, donors: Sequence[str]
    ) -> str:
        donors_seen.append(tuple(donors))
        outcome = await apply_limit_change(
            budget_platform,
            account,
            BudgetPeriod(hard, 1000),
            LimitChange(campaign_id, new_limit),
            BurnRate.zero(),
            NOW,
            BUDGET_RULES,
        )
        return outcome.status.value

    return AutopilotCycle(
        Source(source),
        SqlDecisionLog(sessions, BUSINESS),
        LedgeredActions(ledger, increase),
        SqlAlerts(sessions, BUSINESS),
        lambda: NOW,
        RULES,
    )


def fake(*campaigns: CampaignBudget) -> FakePlatform:
    platform = FakePlatform(list(campaigns), NOW)
    platform.ads = {"ad:bad": True, "ad:good": True}
    return platform


async def rows(sessions: Sessions) -> list[object]:
    return list(await SqlChangeRepository(sessions, BUSINESS).all())


async def test_pause_goes_through_journal_and_reads_result(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()

    await build(data([BAD, GOOD]), platform, sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    assert platform.ads == {"ad:bad": False, "ad:good": True}
    change = (await SqlChangeRepository(sessions, BUSINESS).all())[0]
    assert change.kind is ChangeKind.PAUSE_AD
    assert change.source is ChangeSource.AUTOPILOT
    assert change.before == {"active": True, "archived": False}
    assert change.after == {"active": False, "archived": False}
    assert change.status is ChangeStatus.APPLIED


async def test_every_decision_records_rules_version_and_input(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await build(data([BAD, GOOD]), fake(), sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    logged = await SqlDecisionLog(sessions, BUSINESS).recent("acc")
    bad = next(item for item in logged if item.object_ref == "ad:bad")
    assert bad.rules_version == "autopilot-1"
    assert bad.verdict == "inefficient"
    assert bad.status == "executed"
    assert bad.inputs["spend_micros"] == TARGET * 5
    assert bad.inputs["p_bad_bp"] >= 9000
    change = (await SqlChangeRepository(sessions, BUSINESS).all())[0]
    assert change.decision_id == str(bad.id)


async def test_explanation_has_numbers_next_step_and_undo(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await build(data([BAD, GOOD]), fake(), sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    text_ = (await SqlChangeRepository(sessions, BUSINESS).all())[0].explanation
    assert "2 000 ₽" in text_
    assert "400 ₽" in text_
    assert "Отменить" in text_


def test_money_is_rounded_only_for_display() -> None:
    assert rubles(1_234_567_890) == "1 235 ₽"
    assert rubles(0) == "0 ₽"


async def test_budget_increase_within_reserve_is_applied(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake(CampaignBudget("c1", 1_000_000_000, 0, True, None))
    campaigns = [CampaignStats("c1", 1_000_000_000, True, None)]

    await build(data([GOOD], campaigns), platform, sessions, engine, 10**10).run(
        "acc", TARGET, Mode.AUTO
    )

    assert platform.campaigns["c1"].limit == 1_200_000_000
    assert platform.limit_sum <= 10**10


async def test_budget_guard_can_refuse_autopilot_increase(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    hard = 1_100_000_000
    platform = fake(CampaignBudget("c1", 1_000_000_000, 0, True, None))
    campaigns = [CampaignStats("c1", 1_000_000_000, True, None)]

    await build(data([GOOD], campaigns), platform, sessions, engine, hard).run(
        "acc", TARGET, Mode.AUTO
    )

    assert platform.campaigns["c1"].limit == 1_000_000_000
    logged = await SqlDecisionLog(sessions, BUSINESS).recent("acc")
    assert next(item for item in logged if item.object_ref == "c1").status == "rejected"


async def test_increase_offers_campaigns_without_efficient_ads_as_donors(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    # c2 без эффективных объявлений — с неё можно снять деньги; c3 сама эффективна — нельзя.
    other_bad = AdStats("ad:other", "c2", "g2", TARGET * 5, 0, 30, True)
    other_good = AdStats("ad:third", "c3", "g3", TARGET * 10, 20, 30, True)
    platform = fake(
        CampaignBudget("c1", 1_000_000_000, 0, True, None),
        CampaignBudget("c2", 1_000_000_000, 0, True, None),
        CampaignBudget("c3", 1_000_000_000, 0, True, None),
    )
    campaigns = [
        CampaignStats("c1", 1_000_000_000, True, None),
        CampaignStats("c2", 1_000_000_000, False, None),
        CampaignStats("c3", 1_000_000_000, False, None),
    ]
    donors_seen.clear()

    await build(
        data([GOOD, other_bad, other_good], campaigns), platform, sessions, engine, 10**10
    ).run("acc", TARGET, Mode.AUTO)

    assert donors_seen == [("c2",)]


async def test_untrusted_data_records_alert_and_touches_nothing(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    broken = data([BAD, GOOD], recent=Window(2_000_000_000, 0, 1000, 0))

    await build(broken, platform, sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    assert platform.ads == {"ad:bad": True, "ad:good": True}
    assert await rows(sessions) == []
    assert "data_untrusted" in await SqlAlerts(sessions, BUSINESS).open_kinds()
    logged = await SqlDecisionLog(sessions, BUSINESS).recent("acc")
    assert [item.status for item in logged] == ["protect_only"]
    assert "tracking_lost" in logged[0].inputs["reasons"]


async def test_alert_clears_when_data_recovers(sessions: Sessions, engine: AsyncEngine) -> None:
    broken = data([BAD, GOOD], recent=Window(2_000_000_000, 0, 1000, 0))
    await build(broken, fake(), sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    await build(data([BAD, GOOD]), fake(), sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    assert await SqlAlerts(sessions, BUSINESS).open_kinds() == []


async def test_confirm_mode_changes_nothing_until_approved(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()

    await build(data([BAD, GOOD]), platform, sessions, engine, 10**10).run(
        "acc", TARGET, Mode.CONFIRM
    )

    assert platform.ads["ad:bad"] is True
    assert await rows(sessions) == []
    logged = await SqlDecisionLog(sessions, BUSINESS).recent("acc")
    assert next(i for i in logged if i.object_ref == "ad:bad").status == "awaiting_confirmation"


async def test_platform_failure_leaves_change_for_reconcile(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    platform.faults = [Fault("set_ad_active", after_apply=False)]

    await build(data([BAD, GOOD]), platform, sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    change = (await SqlChangeRepository(sessions, BUSINESS).all())[0]
    assert change.status in {ChangeStatus.PLANNED, ChangeStatus.APPLYING}
    logged = await SqlDecisionLog(sessions, BUSINESS).recent("acc")
    assert next(i for i in logged if i.object_ref == "ad:bad").status == "pending"


async def test_second_cycle_does_not_duplicate_open_change(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    platform.unavailable = True
    cycle = build(data([BAD, GOOD]), platform, sessions, engine, 10**10)
    platform.unavailable = False
    platform.faults = [
        Fault("set_ad_active", after_apply=False),
        Fault("set_ad_active", after_apply=False),
    ]

    await cycle.run("acc", TARGET, Mode.AUTO)
    await cycle.run("acc", TARGET, Mode.AUTO)

    assert len(await rows(sessions)) == 1


async def test_decisions_are_append_only(sessions: Sessions, engine: AsyncEngine) -> None:
    await build(data([BAD, GOOD]), fake(), sessions, engine, 10**10).run("acc", TARGET, Mode.AUTO)

    with pytest.raises(Exception, match="только дополняется"):
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE decisions SET verdict = 'x'"))


def test_explain_refuses_to_invent_missing_numbers() -> None:
    with pytest.raises(KeyError):
        explain("pause_ad", {})


async def test_auto_mode_only_does_what_the_owner_allowed(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    from adhelper.autopilot.domain.decide import ActionKind

    platform = fake()
    cycle = build(data([BAD, GOOD]), platform, sessions, engine, 10**10)

    await cycle.run("acc", TARGET, Mode.AUTO, frozenset({ActionKind.START_TEST}))

    assert platform.ads == {"ad:bad": True, "ad:good": True}  # паузу владелец не разрешал
    logged = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:bad")
    assert logged is not None and logged.status == "awaiting_confirmation"
