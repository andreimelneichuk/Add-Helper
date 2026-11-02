from dataclasses import dataclass
from typing import Protocol

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
