"""Строки для экранов владельца. Только чтение: отсюда ничего не меняет площадку."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class PeriodLine:
    """Бюджетный период кабинета и последнее, что о нём известно. Деньги — микроединицы."""

    account: str
    rules_version: str
    starts_at: datetime
    ends_at: datetime
    hard_limit: int
    reserve_bp: int
    state: str
    stop_pending: bool
    spent: int | None
    data_as_of: datetime | None
    opening_balance: int | None
    opening_spent: int | None
    last_balance: int | None
    balance_checked_at: datetime | None


@dataclass(frozen=True)
class Totals:
    impressions: int
    clicks: int
    cost: int
    conversions: int


@dataclass(frozen=True)
class CampaignLine:
    account: str
    campaign_id: str
    name: str
    state: str
    limit: int | None
    totals: Totals


@dataclass(frozen=True)
class AdLine:
    """Объявление и его показатели за выбранные дни."""

    account: str
    ad_id: str
    campaign_id: str
    title: str
    text: str
    href: str
    state: str
    totals: Totals


@dataclass(frozen=True)
class Scope:
    """Чьи дни показать: всего бизнеса, одного направления или одного объявления."""

    account: str | None = None
    campaign_id: str | None = None
    ad_id: str | None = None


@dataclass(frozen=True)
class DayLine:
    day: date
    totals: Totals


@dataclass(frozen=True)
class AlertLine:
    kind: str
    details: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class HistoryLine:
    """Изменение площадки из журнала или решение автопилота."""

    ref: str
    at: datetime
    source: str
    kind: str
    target: str
    status: str
    explanation: str
    before: Mapping[str, Any]
    after: Mapping[str, Any] | None
    rules_version: str | None


class Dashboard(Protocol):
    """Данные одного бизнеса. Каждый запрос ограничен business_id."""

    async def periods(self, now: datetime) -> list[PeriodLine]: ...
    async def alerts(self) -> list[AlertLine]: ...
    async def campaigns(self, since: date, until: date) -> list[CampaignLine]: ...
    async def ads(self, since: date, until: date, scope: Scope) -> list[AdLine]: ...
    async def days(self, since: date, until: date, scope: Scope) -> list[DayLine]: ...
    async def history(self, limit: int) -> list[HistoryLine]: ...
