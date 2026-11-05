import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any
from uuid import UUID

from adhelper.ledger.domain.model import (
    Change,
    ChangeStatus,
    ExecutionStep,
    ProposedChange,
    decide_execution,
    plan_reached,
)
from adhelper.ledger.domain.ports import (
    AccountLock,
    ChangeRepository,
    GatewayUnavailable,
    PlatformGateway,
    Preflight,
)

logger = logging.getLogger(__name__)


class Ledger:
    """Единый путь изменения: журнал со снимком «до» → исполнитель → чтение результата."""

    def __init__(
        self,
        repository: ChangeRepository,
        gateway: PlatformGateway,
        lock: AccountLock,
        max_attempts: int,
        preflight: Preflight | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._lock = lock
        self._max_attempts = max_attempts
        self._preflight = preflight

    async def submit(self, proposed: ProposedChange) -> Change:
        """Строка журнала и есть задание: она фиксируется одной транзакцией с данными."""
        before = await self._gateway.read(proposed.account, proposed.target_ref)
        return await self._repository.add(proposed, before, ChangeStatus.PLANNED)

    async def record_rejection(self, proposed: ProposedChange, reason: str) -> Change:
        try:
            before = await self._gateway.read(proposed.account, proposed.target_ref)
        except GatewayUnavailable:
            before = {}
        return await self._repository.add(proposed, before, ChangeStatus.REJECTED, reason)

    async def status_of(self, change_id: int) -> ChangeStatus | None:
        return await self._repository.status_of(change_id)

    async def get_change(self, change_id: int) -> Change | None:
        return await self._repository.get(change_id)

    async def changes_on_target(self, account: str, target_ref: str) -> Sequence[Change]:
        return await self._repository.on_target(account, target_ref)

    async def changes_with_key_prefix(self, account: str, prefix: str) -> Sequence[Change]:
        return await self._repository.with_key_prefix(account, prefix)

    async def read_target(self, account: str, target_ref: str) -> Mapping[str, Any]:
        return await self._gateway.read(account, target_ref)

    async def record_external(
        self, observed: ProposedChange, expected: Mapping[str, Any]
    ) -> Change:
        """Изменение вне сервиса: только фиксируем, на площадке уже всё произошло."""
        return await self._repository.add(observed, expected, ChangeStatus.APPLIED)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    async def execute_pending(self, account: str) -> None:
        """На аккаунт одновременно работает один исполнитель; занято — выполнит тот, кто держит."""
        async with self._lock.hold(account) as acquired:
            if not acquired:
                return
            for change in await self._repository.pending(account):
                try:
                    finished = await self._execute(change)
                except GatewayUnavailable:
                    return
                # Незавершённое изменение держит очередь: порядок шагов переноса важен.
                if not finished:
                    return

    async def _execute(self, change: Change) -> bool:
        actual = await self._gateway.read(change.account, change.target_ref)
        step = decide_execution(
            change.before, change.planned, actual, change.attempts, self._max_attempts
        )
        if step is ExecutionStep.FINISH:
            await self._repository.finish(change, ChangeStatus.APPLIED, actual)
            return True
        if step is ExecutionStep.CONFLICT:
            await self._repository.finish(
                change, ChangeStatus.CONFLICT, actual, "changed_outside_service"
            )
            return True
        if step is ExecutionStep.GIVE_UP:
            await self._repository.finish(change, ChangeStatus.FAILED, actual, "attempts_exhausted")
            return True
        if self._preflight is not None:
            reason = await self._preflight(change)
            if reason is not None:
                await self._repository.finish(change, ChangeStatus.FAILED, actual, reason)
                return True
        applying = await self._repository.mark_applying(change)
        await self._gateway.apply(
            applying.account, applying.kind, applying.target_ref, applying.planned
        )
        result = await self._gateway.read(applying.account, applying.target_ref)
        if not plan_reached(applying.planned, result):
            return False
        await self._repository.finish(applying, ChangeStatus.APPLIED, result)
        return True


async def reconcile_open_changes(
    accounts: Iterable[tuple[UUID, str]], ledger_for: Callable[[UUID, str], Ledger | None]
) -> None:
    """Завершает всё незавершённое после перезапуска; сбой одного кабинета не держит остальные."""
    for business_id, account in accounts:
        try:
            ledger = ledger_for(business_id, account)
            if ledger is not None:
                await ledger.execute_pending(account)
        except Exception as error:
            # В логе только тип: текст ошибки может содержать токен или адрес.
            logger.error("Согласование журнала: %s", type(error).__name__)
