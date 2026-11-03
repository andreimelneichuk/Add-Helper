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
    ChangeKind.SET_SCHEDULE: ChangeKind.SET_SCHEDULE,
    ChangeKind.SET_REGIONS: ChangeKind.SET_REGIONS,
}
# Запись журнала нужна HTTP-слою, который сам домен не импортирует.
__all__ = [
    "Change",
    "RequestPreview",
    "RollbackResult",
    "RollbackStatus",
    "apply_request",
    "preview_request",
    "preview_rollback",
    "request_changes",
    "rollback_change",
    "rollback_request",
]
# Просьбы владельца, которые расходятся по направлениям: ключ «вид:запрос:направление».
# Изменения автопилота сюда не входят: каждое из них — своё решение.
REQUEST_PREFIXES = ("schedule:", "regions:", "owner:", "owner-resume:")
OPEN_OR_DONE = {ChangeStatus.PLANNED, ChangeStatus.APPLYING, ChangeStatus.APPLIED}
EXHAUSTED = "attempts_exhausted"


class RollbackStatus(StrEnum):
    READY = "ready"
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
    change: Change | None = None
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


async def _check(
    ledger: Ledger, change_id: int, include_later: bool, overwrite_outside: bool
) -> RollbackResult:
    """Все проверки отмены без действий на площадке. READY значит «можно отменять»."""
    change = await ledger.get_change(change_id)
    if change is None:
        return RollbackResult(RollbackStatus.NOT_FOUND)
    if change.status is not ChangeStatus.APPLIED:
        return RollbackResult(RollbackStatus.NOT_APPLIED, change)
    if change.kind not in INVERSE_KIND or change.source is ChangeSource.EXTERNAL:
        return RollbackResult(RollbackStatus.NOT_REVERSIBLE, change)
    rows = await ledger.changes_on_target(change.account, change.target_ref)
    if _is_reverted(change, rows):
        return RollbackResult(RollbackStatus.ALREADY_REVERTED, change)

    later = _later_changes(change, rows)
    if later and not include_later:
        return RollbackResult(RollbackStatus.HAS_LATER_CHANGES, change, later=later)

    expected = (later[-1] if later else change).after or {}
    try:
        actual = await ledger.read_target(change.account, change.target_ref)
    except GatewayUnavailable:
        return RollbackResult(RollbackStatus.PENDING, change)
    outside = any(actual.get(key) != expected.get(key) for key in change.planned)
    if outside and not overwrite_outside:
        return RollbackResult(RollbackStatus.CHANGED_OUTSIDE, change, later, actual)
    return RollbackResult(RollbackStatus.READY, change, later, actual)


async def preview_rollback(ledger: Ledger, change_id: int) -> RollbackResult:
    """Что случится при отмене, до того как владелец нажал: площадка только читается."""
    return await _check(ledger, change_id, include_later=False, overwrite_outside=False)


async def rollback_change(
    ledger: Ledger,
    change_id: int,
    include_later: bool = False,
    overwrite_outside: bool = False,
) -> RollbackResult:
    """Откат создаёт новые записи тем же путём: политика → журнал → исполнитель → чтение."""
    checked = await _check(ledger, change_id, include_later, overwrite_outside)
    if checked.status is not RollbackStatus.READY or checked.change is None:
        return checked
    for dependent in reversed(checked.later):
        result = await _revert(ledger, dependent)
        if result.status is not RollbackStatus.APPLIED:
            return result
    return await _revert(ledger, checked.change)


def request_prefix(change: Change) -> str | None:
    """Общая часть ключа у изменений одной просьбы владельца, иначе None."""
    if change.source is not ChangeSource.USER:
        return None
    key = change.idempotency_key or ""
    for prefix in REQUEST_PREFIXES:
        rest = key.removeprefix(prefix)
        if rest != key and ":" in rest:
            return prefix + rest.rsplit(":", 1)[0] + ":"
    return None


async def request_changes(ledger: Ledger, change_id: int) -> Sequence[Change]:
    """Изменения той же просьбы по всем направлениям; одиночное — само по себе."""
    change = await ledger.get_change(change_id)
    if change is None:
        return ()
    prefix = request_prefix(change)
    if prefix is None:
        return (change,)
    rows = await ledger.changes_with_key_prefix(change.account, prefix)
    return [
        row
        for row in rows
        if row.kind is change.kind
        and row.source is change.source
        and row.status is ChangeStatus.APPLIED
        and request_prefix(row) == prefix
    ]


@dataclass(frozen=True)
class RequestPreview:
    """Проверка всей просьбы: итог и изменения, которые отмена затронет."""

    result: RollbackResult
    ready: Sequence[Change]
    size: int


async def preview_request(ledger: Ledger, change_id: int) -> RequestPreview:
    """Проверить все направления просьбы, ничего не меняя.

    Уже отменённые части пропускаем. Первая причина «нельзя» становится итогом.
    """
    group = await request_changes(ledger, change_id)
    if not group:
        return RequestPreview(await preview_rollback(ledger, change_id), (), 0)
    ready: list[Change] = []
    first: RollbackResult | None = None
    for change in group:
        checked = await preview_rollback(ledger, change.id)
        if checked.status is RollbackStatus.ALREADY_REVERTED:
            continue
        if checked.status is not RollbackStatus.READY:
            return RequestPreview(checked, (), len(group))
        first = first or checked
        ready.append(change)
    if first is None:
        return RequestPreview(RollbackResult(RollbackStatus.ALREADY_REVERTED, group[0]), (), 0)
    return RequestPreview(first, ready, len(group))


async def rollback_request(ledger: Ledger, change_id: int) -> RollbackResult:
    """Отменить просьбу целиком: сначала проверяем все направления, потом отменяем.

    Если хоть одно нельзя отменить, не трогаем ни одно: половинная отмена хуже понятной причины.
    """
    return await apply_request(ledger, await preview_request(ledger, change_id))


async def apply_request(ledger: Ledger, preview: RequestPreview) -> RollbackResult:
    """Отменить проверенную просьбу: новейшее направление первым, каждое — своей записью."""
    if preview.result.status is not RollbackStatus.READY:
        return preview.result
    for change in reversed(preview.ready):
        result = await _revert(ledger, change)
        if result.status is not RollbackStatus.APPLIED:
            return result
    return RollbackResult(RollbackStatus.APPLIED, preview.result.change)
