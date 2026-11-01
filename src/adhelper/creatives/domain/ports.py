from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from adhelper.creatives.domain.lifecycle import (
    CampaignInfo,
    CampaignSlots,
    Candidate,
    CreativeState,
    NewCreative,
    SlotPlan,
    StoredCreative,
    WritingSet,
)
from adhelper.creatives.domain.model import BusinessProfile, ClaimedSet, CreativeSet


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
