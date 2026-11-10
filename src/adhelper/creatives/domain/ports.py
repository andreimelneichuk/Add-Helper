from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from adhelper.creatives.domain.lifecycle import (
    CampaignInfo,
    CampaignSlots,
    Candidate,
    CreativeState,
    FactPlan,
    LiveAd,
    NewCreative,
    Replacement,
    SlotPlan,
    StoredCreative,
    WritingSet,
)
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    ClaimedSet,
    CreativeSet,
    Rework,
)
from adhelper.creatives.domain.moderation import Checking, ModerationPlan, ModerationRules


class ProfileSource(Protocol):
    async def profile(self, business_id: UUID) -> BusinessProfile | None: ...


class CreativeQueue(Protocol):
    """Очередь наборов поверх всех бизнесов: её разбирает воркер."""

    async def claim(
        self, now: datetime, stale_before: datetime, max_attempts: int
    ) -> ClaimedSet | None: ...
    async def finish(
        self, set_id: int, result: CreativeSet, now: datetime, new: Sequence[NewCreative]
    ) -> None: ...
    async def fail(self, set_id: int, reason: str, now: datetime) -> None: ...


class AdSlots(Protocol):
    """Места одного бизнеса для хранителя. Каждый запрос ограничен business_id."""

    async def campaigns(self) -> Sequence[CampaignSlots]: ...
    async def candidates(self) -> Sequence[Candidate]: ...
    async def open_sets(self) -> frozenset[tuple[str, str]]: ...
    async def sets_since(self, since: datetime) -> int: ...
    async def apply(self, plan: SlotPlan, creative_rules: str, now: datetime) -> None: ...
    async def live_ads(self) -> Sequence[LiveAd]: ...
    async def replacements(self) -> Sequence[Replacement]: ...
    async def rewriting(self) -> frozenset[str]: ...
    async def apply_facts(
        self, plan: FactPlan, creative_rules: str, rework_rules: str, now: datetime
    ) -> None: ...


class Notices(Protocol):
    """Сводка владельцу один раз на ключ. False — уже отправляли."""

    async def notice(self, kind: str, key: str, details: Mapping[str, Any]) -> bool: ...


class Attention(Protocol):
    """Редкие случаи, когда нужен владелец: тревога с письмом и сообщением."""

    async def raise_alert(self, kind: str, details: Mapping[str, Any]) -> None: ...
    async def resolve(self, kind: str) -> None: ...


class Creatives(Protocol):
    """Объявления одного бизнеса для экрана владельца."""

    async def all(self) -> Sequence[StoredCreative]: ...
    async def get(self, creative_id: int) -> StoredCreative | None: ...
    async def writing(self) -> Sequence[WritingSet]: ...
    async def campaigns(self) -> Mapping[tuple[str, str], CampaignInfo]: ...
    async def move(
        self,
        creative_id: int,
        allowed: Sequence[CreativeState],
        state: CreativeState,
        reason: str,
        now: datetime,
    ) -> bool: ...


class ReworkStore(Protocol):
    """Переделки одного бизнеса: объявление площадки и заказ вариантов."""

    async def live_ad(self, account: str, ad_id: str) -> tuple[str, AdDraft] | None: ...
    async def request(
        self,
        account: str,
        campaign_id: str,
        rework: Rework,
        rules_version: str,
        rework_version: str,
        variants: int,
        now: datetime,
    ) -> int: ...


@dataclass(frozen=True)
class Outgoing:
    """Объявление, занявшее место и ждущее отправки на площадку."""

    creative_id: int
    account: str
    campaign_id: str
    draft: AdDraft
    href: str
    # SHA-256 картинки объявления; None — без картинки.
    image: str | None = None
    image_disabled: bool = False


class ModerationStore(Protocol):
    """Отправка и проверка объявлений одного бизнеса. Каждый запрос ограничен business_id."""

    async def accounts(self) -> Sequence[str]: ...
    async def outgoing(self) -> Sequence[Outgoing]: ...
    async def sent(self, creative_id: int, ad_id: str, href: str, now: datetime) -> bool: ...
    async def checking(self) -> Sequence[Checking]: ...
    async def last_rejections(self) -> Mapping[tuple[str, str], str]: ...
    async def apply(
        self,
        plan: ModerationPlan,
        rules: ModerationRules,
        creative_rules: str,
        rework_rules: str,
        now: datetime,
    ) -> Sequence[LiveAd]: ...
    async def replaced(self, account: str, ad_id: str, now: datetime) -> None: ...
    async def waiting_owner(self) -> Sequence[Mapping[str, str]]: ...
    async def on_platform(self, account: str) -> Sequence[tuple[str, str, str]]: ...
    async def owner_stopped(self, account: str, ad_id: str, now: datetime) -> None: ...
    async def owner_resumed(self, account: str, ad_id: str, now: datetime) -> None: ...
    async def lost_tests(self, account: str) -> Sequence[str]: ...
    async def lost_test(self, account: str, ad_id: str, now: datetime) -> None: ...
    async def launched(self, since: datetime, until: datetime) -> Sequence[str]: ...
