from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import (
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)
from adhelper.ledger.domain.ports import GatewayUnavailable

INVERSE_KIND = {
    ChangeKind.SET_LIMIT: ChangeKind.SET_LIMIT,
    # Снятую владельцем паузу можно вернуть; саму паузу снимает только решение с проверкой бюджета.
    ChangeKind.RESUME: ChangeKind.PAUSE,
    ChangeKind.PAUSE_AD: ChangeKind.RESUME_AD,
    ChangeKind.RESUME_AD: ChangeKind.PAUSE_AD,
}
OPEN_OR_DONE = {ChangeStatus.PLANNED, ChangeStatus.APPLYING, ChangeStatus.APPLIED}
EXHAUSTED = "attempts_exhausted"


class RollbackStatus(StrEnum):
    APPLIED = "applied"
    PENDING = "pending"
    NOT_FOUND = "not_found"
    NOT_APPLIED = "not_applied"
    NOT_REVERSIBLE = "not_reversible"
    ALREADY_REVERTED = "already_reverted"
    HAS_LATER_CHANGES = "has_later_changes"
    CHANGED_OUTSIDE = "changed_outside"
    POLICY_REJECTED = "policy_rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class RollbackResult:
    status: RollbackStatus
    later: Sequence[Change] = field(default_factory=tuple)
    actual: Mapping[str, Any] | None = None
    reason: str | None = None


def _is_reverted(change: Change, rows: Sequence[Change]) -> bool:
    return any(r.reverts_change_id == change.id and r.status in OPEN_OR_DONE for r in rows)


def _later_changes(change: Change, rows: Sequence[Change]) -> list[Change]:
    """Действующие изменения поверх этого; пара «изменение и его откат» взаимно гасится."""
    return [
        row
        for row in rows
        if row.id > change.id
        and row.status is ChangeStatus.APPLIED
        and row.reverts_change_id is None
        and row.source is not ChangeSource.EXTERNAL
        and not _is_reverted(row, rows)
    ]


async def _revert(ledger: Ledger, change: Change) -> RollbackResult:
    kind = INVERSE_KIND.get(change.kind)
    if kind is None or change.source is ChangeSource.EXTERNAL:
        # Остановка стражем снимается отдельным решением с проверкой бюджета, а не отменой.
        return RollbackResult(RollbackStatus.NOT_REVERSIBLE)
    if any(key not in change.before for key in change.planned):
        return RollbackResult(RollbackStatus.NOT_REVERSIBLE)
    proposed = ProposedChange(
        account=change.account,
        source=ChangeSource.ROLLBACK,
        kind=kind,
        target_ref=change.target_ref,
        planned={key: change.before[key] for key in change.planned},
        explanation=(
            f"Отменили изменение №{change.id}: вернули прежнее значение. "
            "Потраченные деньги и данные обучения нельзя вернуть."
        ),
        idempotency_key=f"rollback:{change.id}",
        reverts_change_id=change.id,
    )
    try:
        submitted = await ledger.submit(proposed)
        await ledger.execute_pending(change.account)
    except GatewayUnavailable:
        return RollbackResult(RollbackStatus.PENDING)
    current = await ledger.get_change(submitted.id)
    if current is None or current.status in {ChangeStatus.PLANNED, ChangeStatus.APPLYING}:
        return RollbackResult(RollbackStatus.PENDING)
    if current.status is ChangeStatus.APPLIED:
        return RollbackResult(RollbackStatus.APPLIED)
    if current.status is ChangeStatus.FAILED and current.failure_reason != EXHAUSTED:
        return RollbackResult(RollbackStatus.POLICY_REJECTED, reason=current.failure_reason)
    return RollbackResult(RollbackStatus.FAILED, reason=current.failure_reason)


async def rollback_change(
    ledger: Ledger,
    change_id: int,
    include_later: bool = False,
    overwrite_outside: bool = False,
) -> RollbackResult:
    """Откат создаёт новые записи тем же путём: политика → журнал → исполнитель → чтение."""
    change = await ledger.get_change(change_id)
    if change is None:
        return RollbackResult(RollbackStatus.NOT_FOUND)
    if change.status is not ChangeStatus.APPLIED:
        return RollbackResult(RollbackStatus.NOT_APPLIED)
    if change.kind not in INVERSE_KIND or change.source is ChangeSource.EXTERNAL:
        return RollbackResult(RollbackStatus.NOT_REVERSIBLE)
    rows = await ledger.changes_on_target(change.account, change.target_ref)
    if _is_reverted(change, rows):
        return RollbackResult(RollbackStatus.ALREADY_REVERTED)

    later = _later_changes(change, rows)
    if later and not include_later:
        return RollbackResult(RollbackStatus.HAS_LATER_CHANGES, later=later)

    expected = (later[-1] if later else change).after or {}
    try:
        actual = await ledger.read_target(change.account, change.target_ref)
    except GatewayUnavailable:
        return RollbackResult(RollbackStatus.PENDING)
    outside = any(actual.get(key) != expected.get(key) for key in change.planned)
    if outside and not overwrite_outside:
        return RollbackResult(RollbackStatus.CHANGED_OUTSIDE, actual=actual)

    for dependent in reversed(later):
        result = await _revert(ledger, dependent)
        if result.status is not RollbackStatus.APPLIED:
            return result
    return await _revert(ledger, change)
