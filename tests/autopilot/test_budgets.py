"""Лимиты кампаний в данных автопилота берутся с площадки, а не из витрины.

Витрина лимитов не хранит, поэтому без площадки лимит любой кампании был нулём, рост не
планировался никогда, а тест считался «бюджет мал». docs/04-autopilot.md §4.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.application.budgets import CampaignStrategy, WithBudgets
from adhelper.autopilot.domain.budgets import budget_limited
from adhelper.autopilot.domain.health import Window
from adhelper.autopilot.domain.model import AccountData, CampaignStats
from adhelper.budget.domain.limits import BudgetPeriod, CampaignBudget
from adhelper.budget.domain.ports import PlatformUnavailable

NOW = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)
START = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)
PERIOD = BudgetPeriod(hard_limit=3_000_000_000, reserve_bp=1000, starts_at=START, ends_at=END)
RULES = load_rules("autopilot-5")
EMPTY = Window(0, 0, 0, None)


def test_campaign_spending_at_even_pace_is_limited_by_budget() -> None:
    # Половина периода прошла, потрачена половина лимита: площадка сдерживает показы.
    assert budget_limited(1_000_000, 500_000, START, END, NOW, 9000)
    # Потрачено 30% за половину срока: упирается не в деньги, а в спрос.
    assert not budget_limited(1_000_000, 300_000, START, END, NOW, 9000)


def test_exhausted_limit_is_limited_and_unknowns_are_not() -> None:
    assert budget_limited(1_000_000, 1_000_000, START, END, START + timedelta(hours=1), 9000)
    assert not budget_limited(0, 0, START, END, NOW, 9000)
    assert not budget_limited(1_000_000, 900_000, None, END, NOW, 9000)
    assert not budget_limited(1_000_000, 900_000, START, END, START, 9000)
    # Порог 0 выключает признак: так ведут себя прежние правила autopilot-2.
    assert not budget_limited(1_000_000, 1_000_000, START, END, NOW, 0)


@dataclass
class Inner:
    campaigns: list[CampaignStats]

    async def load(self, account: str, now: datetime) -> AccountData:
        return AccountData([], self.campaigns, EMPTY, EMPTY, False, False)


@dataclass
class Platform:
    budgets: list[CampaignBudget]
    down: bool = False
    calls: list[str] = field(default_factory=list)

    async def read_campaigns(self, account: str) -> list[CampaignBudget]:
        self.calls.append(account)
        if self.down:
            raise PlatformUnavailable("direct")
        return self.budgets


async def _period() -> BudgetPeriod | None:
    return PERIOD


async def _strategies(account: str) -> dict[str, CampaignStrategy]:
    return {
        "1": CampaignStrategy(auto=True, learns=False, started=date(2026, 10, 1)),
        "2": CampaignStrategy(auto=True, learns=None, started=None),
    }


async def test_limits_pace_and_strategy_come_from_the_platform() -> None:
    inner = Inner([CampaignStats("1", 0, False, None), CampaignStats("2", 0, False, None)])
    platform = Platform(
        [
            CampaignBudget("1", 1_000_000, 500_000, True, None),
            CampaignBudget("2", 2_000_000, 100_000, True, None),
        ]
    )

    data = await WithBudgets(inner, platform, _period, RULES, _strategies).load("acc", NOW)

    first, second = data.campaigns
    assert (first.limit, first.budget_limited) == (1_000_000, True)
    assert (second.limit, second.budget_limited) == (2_000_000, False)
    assert (first.auto_strategy, first.in_learning) == (True, False)
    # Обучение неизвестно — остаётся неизвестным, политика трактует его строго.
    assert (second.auto_strategy, second.in_learning) == (True, None)
    # Изменений в журнале нет: последним изменением лимита считаем старт кампании.
    assert first.last_change_at == datetime(2026, 10, 1, 0, 0, tzinfo=UTC) - timedelta(hours=3)
    assert second.last_change_at is None


async def test_journal_change_wins_over_campaign_start() -> None:
    changed = NOW - timedelta(days=2)
    inner = Inner([CampaignStats("1", 0, False, changed)])
    platform = Platform([CampaignBudget("1", 1_000_000, 0, True, None)])

    data = await WithBudgets(inner, platform, _period, RULES, _strategies).load("acc", NOW)

    assert data.campaigns[0].last_change_at == changed


async def test_platform_down_keeps_cautious_values() -> None:
    inner = Inner([CampaignStats("1", 0, False, None)])
    platform = Platform([], down=True)

    data = await WithBudgets(inner, platform, _period, RULES, _strategies).load("acc", NOW)

    assert data.campaigns[0].limit == 0
    assert data.campaigns[0].budget_limited is False


async def test_without_period_nothing_is_limited() -> None:
    async def no_period() -> BudgetPeriod | None:
        return None

    inner = Inner([CampaignStats("1", 0, False, None)])
    platform = Platform([CampaignBudget("1", 1_000_000, 1_000_000, True, None)])

    data = await WithBudgets(inner, platform, no_period, RULES, _strategies).load("acc", NOW)

    assert data.campaigns[0].limit == 1_000_000
    assert data.campaigns[0].budget_limited is False
