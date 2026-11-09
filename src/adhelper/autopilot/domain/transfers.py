"""Запись переноса лимита: намерение живёт в базе, пока перенос не доведён или не отменён."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class TransferState(StrEnum):
    OPEN = "open"
    DONE = "done"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"
    # Деньги остались выведенными из донора и не дошли до получателя: нужен человек.
    STUCK = "stuck"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TransferRecord:
    id: str
    account: str
    donor_id: str
    recipient_id: str
    donor_start: int
    donor_target: int
    recipient_target: int
    amount: int
    # Решение политики, по которому перенос разрешён: версия правил и входные данные.
    decision: dict[str, Any]
    state: TransferState = TransferState.OPEN
    created_at: datetime | None = None

    @property
    def recipient_start(self) -> int:
        return self.recipient_target - self.amount


class TransferStore(Protocol):
    """Хранилище переносов одного бизнеса; изоляцию по `business_id` держит адаптер."""

    async def add(self, record: TransferRecord) -> None: ...

    async def get(self, transfer_id: str) -> TransferRecord | None: ...

    async def open_transfers(self) -> list[TransferRecord]: ...

    async def set_state(self, transfer_id: str, state: TransferState) -> None: ...
