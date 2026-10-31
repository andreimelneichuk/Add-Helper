from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from adhelper.ledger.domain.model import Change, ChangeKind, ChangeStatus, ProposedChange


class GatewayUnavailable(Exception):
    """Площадка не ответила; применилось ли действие, неизвестно."""


class PlatformGateway(Protocol):
    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]: ...
    async def apply(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None: ...


class ChangeRepository(Protocol):
    async def status_of(self, change_id: int) -> ChangeStatus | None: ...
    async def add(
        self,
        proposed: ProposedChange,
        before: Mapping[str, Any],
        status: ChangeStatus,
        reason: str | None = None,
    ) -> Change: ...
    async def pending(self, account: str) -> Sequence[Change]: ...
    async def mark_applying(self, change: Change) -> Change: ...
    async def finish(
        self,
        change: Change,
        status: ChangeStatus,
        after: Mapping[str, Any] | None,
        reason: str | None = None,
    ) -> Change: ...


class AccountLock(Protocol):
    def hold(self, account: str) -> AbstractAsyncContextManager[bool]: ...


# Проверка политики прямо перед применением: возвращает причину отказа или None.
Preflight = Callable[[Change], Awaitable[str | None]]
