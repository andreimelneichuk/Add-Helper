from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from adhelper.creatives.domain.model import (
    BusinessProfile,
    ClaimedSet,
    CreativeSet,
    DraftDecision,
    StoredSet,
)


class ProfileSource(Protocol):
    async def profile(self, business_id: UUID) -> BusinessProfile | None: ...


class CreativeQueue(Protocol):
    """Очередь наборов поверх всех бизнесов: её разбирает воркер."""

    async def claim(
        self, now: datetime, stale_before: datetime, max_attempts: int
    ) -> ClaimedSet | None: ...
    async def finish(self, set_id: int, result: CreativeSet, now: datetime) -> None: ...
    async def fail(self, set_id: int, reason: str, now: datetime) -> None: ...


class CreativeSets(Protocol):
    """Наборы одного бизнеса. Каждый запрос ограничен business_id."""

    async def request(self, rules_version: str, now: datetime) -> int: ...
    async def count_since(self, since: datetime) -> int: ...
    async def latest(self, limit: int) -> Sequence[StoredSet]: ...
    async def decide(self, set_id: int, index: int, decision: DraftDecision) -> bool: ...
