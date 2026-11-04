from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


class AdPublisher(Protocol):
    """Новые объявления в существующей кампании и чтение их проверки.

    Создание на площадке не идемпотентно, поэтому повтор начинается с поиска по метке:
    нашлось — второй раз не создаём. Как метка хранится на площадке, решает адаптер.
    Сбой связи адаптер сообщает исключением площадки; создалось ли объявление, тогда неизвестно.
    """

    async def find_ad(self, account: str, marker: str) -> PublishedAd | None: ...
    async def create_ad(
        self, account: str, campaign_id: str, marker: str, headline: str, text: str, href: str
    ) -> None: ...
    async def moderation(
        self, account: str, ad_ids: Sequence[str]
    ) -> Sequence[ModerationVerdict]: ...
