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


@dataclass(frozen=True)
class AdImage:
    """Картинка объявления, готовая к загрузке: уже JPEG по требованиям площадки."""

    sha256: str
    data: bytes
    # Имя в библиотеке картинок площадки: только наш номер, без данных бизнеса.
    name: str


class ImageUploader(Protocol):
    """Загрузка картинки в библиотеку площадки. Одинаковая картинка хранится там один раз,
    поэтому повтор загрузки безопасен и возвращает тот же номер."""

    async def upload_image(self, account: str, image: AdImage) -> str: ...


class ImageVault(Protocol):
    """Картинки бизнеса для исполнителя журнала: в журнале только sha256, байты здесь."""

    async def image_by_sha(self, sha256: str) -> AdImage | None: ...
    async def platform_hash(self, account: str, sha256: str) -> str | None: ...
    async def remember_hash(self, account: str, sha256: str, image_hash: str) -> None: ...
    async def sha_of(self, account: str, image_hash: str) -> str | None: ...


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
        image_hash: str | None = None,
        group_name: str | None = None,
        keywords: Sequence[str] = (),
    ) -> None:
        """Без `group_id` объявление встаёт в первую группу кампании.

        `image_hash` — номер картинки, уже загруженной на площадку (`ImageUploader`).
        `group_name` и `keywords` задают отдельное направление объявлений.
        """
        ...

    async def ad_group(self, account: str, ad_id: str) -> str | None:
        """Группа объявления на площадке; нет такого объявления — None."""
        ...

    async def moderation(
        self, account: str, ad_ids: Sequence[str]
    ) -> Sequence[ModerationVerdict]: ...


@dataclass(frozen=True)
class CampaignDay:
    """Строка отчёта площадки по кампании за сутки площадки. Деньги в микроединицах."""

    campaign_id: str
    day: date
    impressions: int
    clicks: int
    cost: int
    conversions: int


@dataclass(frozen=True)
class CampaignInventory:
    """Current campaign details used to refresh the owner's local view."""

    campaign_id: str
    name: str
    state: str


@dataclass(frozen=True)
class AdInventory:
    """Current ad details and serving state from the advertising platform."""

    ad_id: str
    campaign_id: str
    title: str
    text: str
    href: str
    state: str


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

    async def campaign_days(
        self, account: str, campaign_ids: Sequence[str], since: date, until: date
    ) -> Sequence[CampaignDay]: ...


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
