from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Protocol


class AdControl(Protocol):
    """Включение и приостановка объявлений. Удаления нет: откат должен оставаться возможным."""

    async def read_ad(self, account: str, ad_ref: str) -> Mapping[str, Any]: ...
    async def set_ad_active(self, account: str, ad_ref: str, active: bool) -> None: ...


class ModerationState(StrEnum):
    """Итог проверки объявления площадкой нашими словами; формат площадки — в адаптере."""

    MODERATION = "moderation"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ModerationVerdict:
    ad_id: str
    state: ModerationState
    # Причины отказа текстом площадки. Это данные: код сводит их к закрытому набору.
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishedAd:
    """Объявление, найденное на площадке по нашей метке."""

    ad_id: str
    campaign_id: str
    headline: str
    text: str
    href: str
    # Номер картинки на площадке (в Директе AdImageHash); None — объявление без картинки.
    image_hash: str | None = None
    # Группа объявлений на площадке: вариант A/B-теста должен встать в группу контроля.
    group_id: str = ""


class AdPublisher(Protocol):
    """Новые объявления в существующей кампании и чтение их проверки.

    Создание на площадке не идемпотентно, поэтому повтор начинается с поиска по метке:
    нашлось — второй раз не создаём. Как метка хранится на площадке, решает адаптер.
    Сбой связи адаптер сообщает исключением площадки; создалось ли объявление, тогда неизвестно.
    """

    async def find_ad(self, account: str, marker: str) -> PublishedAd | None: ...
    async def create_ad(
        self,
        account: str,
        campaign_id: str,
        marker: str,
        headline: str,
        text: str,
        href: str,
        group_id: str | None = None,
    ) -> None:
        """Без `group_id` объявление встаёт в первую группу кампании."""
        ...

    async def ad_group(self, account: str, ad_id: str) -> str | None:
        """Группа объявления на площадке; нет такого объявления — None."""
        ...

    async def moderation(
        self, account: str, ad_ids: Sequence[str]
    ) -> Sequence[ModerationVerdict]: ...


@dataclass(frozen=True)
class AdDay:
    """Строка отчёта площадки по объявлению за сутки площадки. Деньги в микроединицах."""

    ad_id: str
    day: date
    impressions: int
    clicks: int
    cost: int
    conversions: int


class AdReports(Protocol):
    """Отчёт площадки по объявлениям кампаний за сутки (в Директе `Reports` по `AdId`).

    Только чтение. Числа — данные площадки, их не пересчитываем.
    """

    async def ad_days(
        self, account: str, campaign_ids: Sequence[str], since: date, until: date
    ) -> Sequence[AdDay]: ...


# Цель записи журнала о данных рекламодателя для маркировки: одна на кабинет.
CLIENT_PREFIX = "client:"


@dataclass(frozen=True)
class Advertiser:
    """Рекламодатель для маркировки (docs/03-integrations.md «Маркировка»).

    ИНН и телефон — реквизиты: в журнал, логи и модели не попадают, в журнале только `card`.
    """

    kind: str
    inn: str
    name: str
    phone: str | None
    okved: str | None

    def card(self) -> dict[str, Any]:
        """Что можно хранить в журнале: по этому сверяем цель и результат."""
        return {"kind": self.kind, "inn_tail": self.inn[-4:], "okved": self.okved}


class AdvertiserRegistry(Protocol):
    """Данные рекламодателя кабинета на площадке (в Директе `Clients.update`).

    Повтор безопасен: запись заменяет прежние данные, повтор начинается с чтения.
    """

    async def read_advertiser(self, account: str) -> Advertiser | None: ...
    async def set_advertiser(self, account: str, advertiser: Advertiser) -> None: ...
