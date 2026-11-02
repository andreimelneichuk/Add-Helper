from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.campaigns.domain.launch import NewCampaign
from adhelper.campaigns.domain.targeting import Schedule

# Цель записи журнала о показе: отдельно от бюджета кампании, у них разная история.
TARGETING_PREFIX = "targeting:"


@dataclass(frozen=True)
class Targeting:
    """Где и когда кампания показывается сейчас, по чтению с площадки."""

    schedule: Schedule
    regions: tuple[int, ...]


class TargetingControl(Protocol):
    """Расписание и регионы кампании. Формат площадки остаётся в адаптере.

    Сбой связи адаптер сообщает исключением площадки; применилось ли действие, тогда неизвестно.
    """

    async def read_targeting(self, account: str, campaign_id: str) -> Targeting | None: ...
    async def set_schedule(self, account: str, campaign_id: str, schedule: Schedule) -> None: ...
    async def set_regions(
        self, account: str, campaign_id: str, regions: tuple[int, ...]
    ) -> None: ...


@dataclass(frozen=True)
class CreatedCampaign:
    """Кампания запуска, найденная на площадке по нашей метке."""

    campaign_id: str
    campaign: NewCampaign
    ad_ids: tuple[str, ...]


class CampaignLauncher(Protocol):
    """Создание кампаний запуска. Многие методы площадки не идемпотентны, поэтому повтор
    начинается с поиска по метке: нашлась — второй раз не создаём.

    Сбой связи адаптер сообщает исключением площадки; создалась ли кампания, тогда неизвестно.
    """

    async def find_campaign(self, account: str, marker: str) -> CreatedCampaign | None: ...
    async def create_campaign(self, account: str, campaign: NewCampaign) -> None: ...


class ActivePeriodExists(Exception):
    """У кабинета уже идёт бюджетный период: реклама запущена не этим мастером."""


@dataclass(frozen=True)
class StoredLaunch:
    id: int
    account: str
    period: BudgetPeriod
    rules_version: str


class LaunchStore(Protocol):
    """Запуск бизнеса. Строка и период бюджета появляются одной транзакцией."""

    async def get(self) -> StoredLaunch | None: ...
    async def start(
        self,
        account: str,
        period: BudgetPeriod,
        rules_version: str,
        budget_rules_version: str,
        now: datetime,
    ) -> StoredLaunch: ...
